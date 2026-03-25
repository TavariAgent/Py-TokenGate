# -*- coding: utf-8 -*-
# gui/app.py
"""
TokenGate - Web GUI Dashboard

Flask-based real-time monitoring and control interface for the threading engine.

Features:
- Password-protected access (set on first run)
- Optional IP whitelist
- Real-time metrics via WebSocket
- Admin controls (kill tokens, pause/resume, etc.)
- Live core affinity visualization
- Guard House heatmap
"""
import os
import sys
import json
import time
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit, disconnect
from functools import wraps

try:
    from ..operations_coordinator import OperationsCoordinator, get_global_coordinator
    from ..storage_throttle import configure_storage_throttle
    from ..token_system import global_token_pool, TokenState
except ImportError as e:
    print(f"Import error: {e}")
    raise

from .auth import AuthManager

# ============================================================================
# Flask App Setup
# ============================================================================

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
    static_folder=os.path.join(os.path.dirname(__file__), 'static')
)
app.config['SECRET_KEY'] = os.urandom(24)  # Random secret key for sessions
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Auth manager
auth_mgr = AuthManager()

# Global coordinator instance (created on startup)
coordinator: Optional[OperationsCoordinator] = None
metrics_thread = None
running = False

# ============================================================================
# Authentication Decorators
# ============================================================================

def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def ip_whitelist_check(f):
    """Decorator to check IP whitelist."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        
        if not auth_mgr.check_ip_allowed(client_ip):
            return jsonify({'error': 'IP not whitelisted'}), 403
        
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# Routes - Authentication
# ============================================================================

@app.route('/')
def index():
    """Root route - redirect to dashboard or setup."""
    if not auth_mgr.is_initialized():
        return redirect(url_for('setup'))
    
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    return redirect(url_for('dashboard'))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """First-run setup page."""
    if auth_mgr.is_initialized():
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm = request.form.get('confirm')
        
        if password != confirm:
            return render_template('setup.html', error='Passwords do not match')
        
        if len(password) < 8:
            return render_template('setup.html', error='Password must be at least 8 characters')
        
        # Optional: Set IP whitelist
        whitelist_enabled = request.form.get('whitelist_enabled') == 'on'
        whitelist_ips = []
        
        if whitelist_enabled:
            whitelist_str = request.form.get('whitelist_ips', '')
            whitelist_ips = [ip.strip() for ip in whitelist_str.split(',') if ip.strip()]
        
        # Initialize auth
        auth_mgr.initialize(password, whitelist_ips)
        
        # Auto-login after setup
        session['logged_in'] = True
        
        return redirect(url_for('dashboard'))
    
    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
@ip_whitelist_check
def login():
    """Login page."""
    if not auth_mgr.is_initialized():
        return redirect(url_for('setup'))
    
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        
        if auth_mgr.verify_password(password):
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        
        return render_template('login.html', error='Invalid password')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout route."""
    session.pop('logged_in', None)
    return redirect(url_for('login'))


# ============================================================================
# Routes - Dashboard
# ============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard page."""
    return render_template('dashboard.html')


@app.route('/api/stats')
@login_required
def get_stats():
    """API endpoint for stats (REST fallback)."""
    if coordinator is None:
        return jsonify({'error': 'Coordinator not initialized'}), 503

    stats = coordinator.get_stats()
    return jsonify(stats)


# ============================================================================
# Routes - Admin Controls
# ============================================================================
@app.route('/api/launcher/tasks')
@login_required
def get_launcher_tasks():
    """Get all registered tasks for launcher UI."""
    try:
        import launcher_config
        import importlib
        importlib.reload(launcher_config)  # Reload to get latest config

        tasks = launcher_config.REGISTERED_TASKS
        categories = launcher_config.CATEGORIES

        # Organize tasks by category
        organized = {}
        for task_id, task_info in tasks.items():
            # Skip disabled tasks
            if not task_info.get('enabled', True):
                continue

            category = task_info.get('category', 'Custom')
            if category not in organized:
                organized[category] = []

            organized[category].append({
                'id': task_id,
                'description': task_info.get('description', ''),
                'icon': task_info.get('icon', '⚙️')
            })

        return jsonify({
            'tasks': organized,
            'categories': categories
        })

    except Exception as e:
        return jsonify({
            'error': str(e),
            'tasks': {},
            'categories': {}
        })

@app.route('/api/launcher/run/<task_id>', methods=['POST'])
@login_required
def run_launcher_task(task_id):
    """Run a registered task by ID."""
    try:
        import launcher_config
        import importlib
        importlib.reload(launcher_config)

        # Get task config
        if task_id not in launcher_config.REGISTERED_TASKS:
            return jsonify({
                'status': 'error',
                'error': f'Task "{task_id}" not found'
            }), 404

        task = launcher_config.REGISTERED_TASKS[task_id]

        # Check if enabled
        if not task.get('enabled', True):
            return jsonify({
                'status': 'error',
                'error': f'Task "{task_id}" is disabled'
            }), 403

        module_name = task['module']
        function_name = task['function']

        # Prefix with package name for absolute import
        full_module_name = f"TokenGate.{module_name}"
        module = importlib.import_module(full_module_name)

        # Get the function
        func = getattr(module, function_name)

        # Execute it!
        func()

        return jsonify({
            'status': 'success',
            'message': f'Task "{task_id}" completed successfully!',
            'task_id': task_id
        })

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[LAUNCHER ERROR]\n{tb}")  # prints full chain to terminal
        return jsonify({
            'status': 'error',
            'error': str(e),
            'traceback': tb
        }), 500

@app.route('/api/recent_executions')
@login_required
def get_recent_executions():
    """API endpoint for recent executions."""
    if coordinator is None:
        return jsonify({'error': 'Coordinator not initialized'}), 503

    limit = request.args.get('limit', 50, type=int)
    executions = coordinator.get_recent_executions(limit=limit)

    return jsonify({
        'executions': executions,
        'total': len(executions)
    })


@app.route('/api/admin/dump_history', methods=['POST'])
@login_required
def admin_dump_history():
    if coordinator is None:
        return jsonify({'error': 'Coordinator not initialized'}), 503

    try:
        dump_dir = Path(__file__).parent.parent / 'dump'
        dump_dir.mkdir(exist_ok=True)

        timestamp = int(time.time())
        filepath = dump_dir / f'execution_history_{timestamp}.json'

        result = coordinator.dump_execution_history(filepath=filepath)
        return jsonify({
            'success': True,
            'filepath': result,
            'message': f'Execution history dumped to {result}'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/admin/pause', methods=['POST'])
@login_required
def admin_pause():
    """Pause admission."""
    if coordinator:
        coordinator.pause_admission()
        return jsonify({'status': 'paused'})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/resume', methods=['POST'])
@login_required
def admin_resume():
    """Resume admission."""
    if coordinator:
        coordinator.resume_admission()
        return jsonify({'status': 'resumed'})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/kill_token', methods=['POST'])
@login_required
def admin_kill_token():
    """Kill specific token."""
    data = request.json
    token_id = data.get('token_id')
    reason = data.get('reason', 'admin_override')
    
    if coordinator:
        success = coordinator.kill_token(token_id, reason)
        return jsonify({'success': success, 'token_id': token_id})
    
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/pause_operation', methods=['POST'])
@login_required
def admin_pause_operation():
    """Pause admission for a specific operation type."""
    data = request.json
    op_type = data.get('operation_type')

    if coordinator:
        # NOTE: You will need to implement pause_operation in your OperationsCoordinator
        success = coordinator.pause_operation(op_type)
        return jsonify({'success': success, 'operation_type': op_type})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/resume_operation', methods=['POST'])
@login_required
def admin_resume_operation():
    """Resume admission for a specific operation type."""
    data = request.json
    op_type = data.get('operation_type')

    if coordinator:
        # NOTE: You will need to implement resume_operation in your OperationsCoordinator
        success = coordinator.resume_operation(op_type)
        return jsonify({'success': success, 'operation_type': op_type})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/drain_operation', methods=['POST'])
@login_required
def admin_drain_operation():
    """Drain all waiting tokens for a specific operation type."""
    data = request.json
    op_type = data.get('operation_type')

    if coordinator:
        # NOTE: You will need to implement drain_operation in your OperationsCoordinator
        count = coordinator.drain_operation(op_type)
        return jsonify({'drained': count, 'operation_type': op_type})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/kill_operation', methods=['POST'])
@login_required
def admin_kill_operation():
    """Kill all tokens of operation type."""
    data = request.json
    operation_type = data.get('operation_type')
    reason = data.get('reason', 'admin_bulk_kill')
    
    if coordinator:
        count = coordinator.kill_all_by_operation(operation_type, reason)
        return jsonify({'killed': count, 'operation_type': operation_type})
    
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/drain', methods=['POST'])
@login_required
def admin_drain():
    """Emergency drain all waiting tokens."""
    if coordinator:
        count = coordinator.drain_pool()
        return jsonify({'drained': count})
    
    return jsonify({'error': 'No coordinator'}), 503


# ===============
# WebSocket
# ===============

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection."""
    if not session.get('logged_in'):
        return False

    print(f"[GUI] Client connected: {request.sid}")
    emit('status', {'message': 'Connected to TokenGate Dashboard'})
    return None


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection."""
    print(f"[GUI] Client disconnected: {request.sid}")


@socketio.on('request_update')
def handle_update_request():
    """Handle client requesting stats update."""
    if coordinator:
        send_stats_update()


def send_stats_update():
    """Send complete stats update to all clients."""
    if coordinator is None:
        return
    
    try:
        # Gather all stats
        stats = coordinator.get_stats()
        guard_stats = coordinator.guard_house.get_stats()
        overflow_stats = coordinator.overflow_guard.get_stats()

        recent_executions = coordinator.get_recent_executions(limit=20)
        
        # Get token details
        all_tokens = global_token_pool.get_all_tokens()
        token_details = []
        
        for token in list(all_tokens.values())[:50]:  # Limit to 50 most recent
            token_details.append(token.get_status())
        
        # Affinity distribution
        affinity_report = coordinator.affinity_queue.get_affinity_report()
        
        # Compile full update
        update = {
            'timestamp': time.time(),
            'system': stats,
            'guard_house': guard_stats,
            'overflow_guard': overflow_stats,
            'tokens': token_details,
            'affinity': affinity_report,
            'recent_executions': recent_executions
        }
        socketio.emit('stats_update', update)
    
    except Exception as e:
        print(f"[GUI] Error sending stats: {e}")


def metrics_broadcast_loop():
    """Background thread to broadcast metrics."""
    global running
    
    while running:
        send_stats_update()
        time.sleep(1.0)  # Update every second


# ============================================================================
# Coordinator Management
# ============================================================================

def start_coordinator():
    """Start the global operations coordinator."""
    global coordinator, metrics_thread, running

    print("[GUI] Starting operations coordinator...")

    # Use global singleton (creates if needed, returns existing if present)
    coordinator = get_global_coordinator()

    # Start metrics broadcast
    running = True
    metrics_thread = threading.Thread(
        target=metrics_broadcast_loop,
        daemon=True,
        name="GUI-Metrics-Broadcast"
    )
    metrics_thread.start()

    print("[GUI] Coordinator started successfully")

def stop_coordinator():
    """Stop metrics broadcast (but don't stop coordinator - it's global!)."""
    global running

    if coordinator:
        print("[GUI] Stopping metrics broadcast...")
        running = False
        coordinator.stop()
        print("[GUI] Metrics broadcast stopped")


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == '__main__':
    print("=" * 70)
    print("TokenGate - WEB GUI DASHBOARD")
    print("=" * 70)
    print()
    
    # Start coordinator
    start_coordinator()
    
    # Get local IP
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"Dashboard accessible at:")
    print(f"  Local:   http://localhost:5000")
    print(f"  Network: http://{local_ip}:5000")
    print()
    print("Starting Flask server...")
    print()
    
    try:
        # Run Flask with SocketIO
        socketio.run(
            app,
            host='0.0.0.0',  # Listen on all interfaces
            port=5000,
            debug=False,
            use_reloader=False
        )
    
    finally:
        # Cleanup
        stop_coordinator()
        print("\n[GUI] Shutdown complete")
