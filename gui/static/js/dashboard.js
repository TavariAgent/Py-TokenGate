// gui/static/js/dashboard.js
// TokenGate Dashboard - Real-time Updates

let socket;
let connected = false;
let startTime = Date.now();

// ============================================================================
// WebSocket Connection
// ============================================================================

function initWebSocket() {
    socket = io();
    
    socket.on('connect', function() {
        console.log('Connected to server');
        connected = true;
        updateConnectionStatus(true);
        
        // Request initial update
        socket.emit('request_update');
    });
    
    socket.on('disconnect', function() {
        console.log('Disconnected from server');
        connected = false;
        updateConnectionStatus(false);
    });
    
    socket.on('status', function(data) {
        console.log('Status:', data.message);
    });
    
    socket.on('stats_update', function(data) {
        updateDashboard(data);
            // Update recent executions with NEW data structure
        if (data.recent_executions && data.recent_executions.length > 0) {
            updateRecentExecutions(data.recent_executions);
        }
    });
}

function updateConnectionStatus(isConnected) {
    const indicator = document.getElementById('connection-status');
    
    if (isConnected) {
        indicator.style.color = '#10b981';
        indicator.classList.remove('disconnected');
    } else {
        indicator.style.color = '#ef4444';
        indicator.classList.add('disconnected');
    }
}

// ============================================================================
// Dashboard Updates
// ============================================================================

function updateDashboard(data) {
    updateSystemStatus(data.system);
    updateTokenPool(data.system.token_pool);
    updateCoreAffinity(data.affinity);
    updateGuardHouse(data.guard_house);
    updateOverflowGuard(data.overflow_guard);
    updateAdmissionGate(data.system.admission_gate);
    updateTimestamp();
}

function updateSystemStatus(system) {
    const topology = system.topology;
    const workerQueue = system.worker_queue;
    
    document.getElementById('cores-count').textContent = topology.physical_cores;
    document.getElementById('workers-total').textContent = topology.total_workers;
    
    // Calculate busy/idle from recent execution data
    const queueStats = workerQueue || {};
    const executing = queueStats.total_executed || 0;
    
    document.getElementById('worker-pattern').textContent = `${topology.workers_per_core} per core`;
    
    // Calculate uptime
    const uptime = Math.floor((Date.now() - startTime) / 1000);
    document.getElementById('uptime').textContent = formatUptime(uptime);
}

function updateTokenPool(tokenPool) {
    document.getElementById('tokens-created').textContent = tokenPool.total_created || 0;
    
    const byState = tokenPool.tokens_by_state || {};
    document.getElementById('tokens-waiting').textContent = byState.waiting || 0;
    document.getElementById('tokens-executing').textContent = byState.executing || 0;
    document.getElementById('tokens-completed').textContent = byState.completed || 0;
    document.getElementById('tokens-failed').textContent = byState.failed || 0;
}

function updateCoreAffinity(affinity) {
    const container = document.getElementById('core-affinity-container');
    container.innerHTML = '';
    
    if (!affinity || !affinity.affinity_distribution) {
        container.innerHTML = '<p style="color: #64748b;">No affinity data available</p>';
        return;
    }
    
    const distribution = affinity.affinity_distribution;
    
    // Sort cores by ID
    const coreIds = Object.keys(distribution).sort((a, b) => {
        const numA = parseInt(a.replace('core_', ''));
        const numB = parseInt(b.replace('core_', ''));
        return numA - numB;
    });
    
    coreIds.forEach(coreId => {
        const data = distribution[coreId];
        const total = data.total_tasks || 0;
        
        if (total === 0) {
            return; // Skip cores with no tasks
        }
        
        const heavy = data.heavy || 0;
        const medium = data.medium || 0;
        const light = data.light || 0;
        
        const coreBar = document.createElement('div');
        coreBar.className = 'core-bar';
        
        const coreNum = coreId.replace('core_', '');
        
        coreBar.innerHTML = `
            <div class="core-label">
                <strong>Core ${coreNum}</strong>
                <span>${total} tasks</span>
            </div>
            <div class="core-distribution">
                ${heavy > 0 ? `<div class="core-segment heavy" style="width: ${heavy}%" title="Heavy: ${heavy.toFixed(1)}%">${heavy > 15 ? heavy.toFixed(0) + '%' : ''}</div>` : ''}
                ${medium > 0 ? `<div class="core-segment medium" style="width: ${medium}%" title="Medium: ${medium.toFixed(1)}%">${medium > 15 ? medium.toFixed(0) + '%' : ''}</div>` : ''}
                ${light > 0 ? `<div class="core-segment light" style="width: ${light}%" title="Light: ${light.toFixed(1)}%">${light > 15 ? light.toFixed(0) + '%' : ''}</div>` : ''}
            </div>
        `;
        
        container.appendChild(coreBar);
    });
}

function updateGuardHouse(guardHouse) {
    document.getElementById('guard-methods').textContent = guardHouse.total_methods_tracked || 0;
    document.getElementById('guard-executions').textContent = guardHouse.total_executions_monitored || 0;
    
    const healthDist = guardHouse.health_distribution || {};
    const container = document.getElementById('health-distribution');
    
    container.innerHTML = `
        <div class="health-stat health-excellent">
            <span class="health-stat-count">${healthDist.excellent || 0}</span>
            <span class="health-stat-label">Excellent</span>
        </div>
        <div class="health-stat health-healthy">
            <span class="health-stat-count">${healthDist.healthy || 0}</span>
            <span class="health-stat-label">Healthy</span>
        </div>
        <div class="health-stat health-at-risk">
            <span class="health-stat-count">${healthDist.at_risk || 0}</span>
            <span class="health-stat-label">At Risk</span>
        </div>
        <div class="health-stat health-problematic">
            <span class="health-stat-count">${healthDist.problematic || 0}</span>
            <span class="health-stat-label">Problem</span>
        </div>
    `;
}

function updateOverflowGuard(overflow) {
    document.getElementById('overflow-inspections').textContent = overflow.total_inspections || 0;
    document.getElementById('overflow-retries').textContent = overflow.total_retries_created || 0;
    document.getElementById('overflow-succeeded').textContent = overflow.total_retries_succeeded || 0;
    document.getElementById('overflow-exhausted').textContent = overflow.total_retries_exhausted || 0;
}

function updateAdmissionGate(gate) {
    document.getElementById('gate-admitted').textContent = gate.total_admitted || 0;
    document.getElementById('gate-expired').textContent = gate.total_expired || 0;
    document.getElementById('gate-rate').textContent = (gate.current_rate || 0) + '/s';
}

function updateRecentExecutions(executions) {
    const container = document.getElementById('execution-list');

    if (!executions || executions.length === 0) {
        container.innerHTML = '<p style="color: #64748b; text-align: center; padding: 20px;">No recent executions</p>';
        return;
    }

    // Show last 10 executions
    const recent = executions.slice(0, 10);

    container.innerHTML = recent.map(exec => {
        // Status symbol based on success
        const status = exec.success ? '✓' : '✗';
        const statusClass = exec.success ? 'metric-success' : 'metric-danger';

        // Format execution time (convert to milliseconds for display)
        const execTimeMs = (exec.execution_time * 1000).toFixed(2);
        const execTime = execTimeMs + 'ms';

        // Format timestamp as time (HH:MM:SS)
        const time = new Date(exec.timestamp * 1000).toLocaleTimeString();

        return `
            <div class="execution-item">
                <span class="execution-name">${exec.operation_type}</span>
                <div class="execution-details">
                    <span class="execution-method" style="color: #64748b; font-size: 0.85em;">${exec.method_name}</span>
                    <span class="execution-core" style="color: #8b5cf6;">Core ${exec.core_id}</span>
                    <span class="execution-time">${execTime}</span>
                    <span class="execution-status ${statusClass}">${status}</span>
                </div>
            </div>
        `;
    }).join('');
}

function updateTimestamp() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString();
    document.getElementById('update-time').textContent = timeStr;
}

// ============================================================================
// Task Launcher
// ============================================================================

let runningTasks = new Set();

function loadLauncher() {
    fetch('/api/launcher/tasks')
        .then(res => res.json())
        .then(data => {
            renderLauncher(data.tasks, data.categories);
        })
        .catch(err => {
            console.error('Failed to load launcher:', err);
        });
}

function renderLauncher(tasks, categories) {
    const container = document.getElementById('launcher-container');

    if (!tasks || Object.keys(tasks).length === 0) {
        container.innerHTML = `
            <p style="color: #64748b; text-align: center; padding: 20px;">
                No tasks registered. Add tasks to <code>launcher_config.py</code>
            </p>
        `;
        return;
    }

    // Sort categories by order
    const sortedCategories = Object.keys(tasks).sort((a, b) => {
        const orderA = categories[a]?.order || 999;
        const orderB = categories[b]?.order || 999;
        return orderA - orderB;
    });

    let html = '';

    sortedCategories.forEach(category => {
        const categoryTasks = tasks[category];
        const categoryColor = categories[category]?.color || '#6366f1';

        html += `
            <div class="launcher-category">
                <h4 class="launcher-category-title" style="color: ${categoryColor};">
                    ${category}
                </h4>
                <div class="launcher-tasks">
        `;

        categoryTasks.forEach(task => {
            const isRunning = runningTasks.has(task.id);
            const buttonClass = isRunning ? 'btn-disabled' : 'btn-success';
            const buttonText = isRunning ? 'Running...' : 'Run';

            html += `
                <div class="launcher-task">
                    <div class="launcher-task-info">
                        <span class="launcher-task-icon">${task.icon}</span>
                        <div>
                            <strong>${task.id}</strong>
                            <p class="launcher-task-desc">${task.description}</p>
                        </div>
                    </div>
                    <button
                        class="btn btn-small ${buttonClass}"
                        onclick="runTask('${task.id}')"
                        ${isRunning ? 'disabled' : ''}
                        id="task-btn-${task.id}"
                    >
                        ${buttonText}
                    </button>
                </div>
            `;
        });

        html += `
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function runTask(taskId) {
    if (runningTasks.has(taskId)) {
        return;
    }

    if (!confirm(`Run task: ${taskId}?`)) {
        return;
    }

    // Mark as running
    runningTasks.add(taskId);
    updateTaskButton(taskId, true);

    fetch(`/api/launcher/run/${taskId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(res => res.json())
    .then(data => {
        runningTasks.delete(taskId);
        updateTaskButton(taskId, false);

        if (data.status === 'success') {
            alert(`✅ ${data.message}\n\nCheck Recent Executions for results!`);
        } else {
            alert(`❌ Error: ${data.error}`);
            console.error('Task error:', data);
        }
    })
    .catch(err => {
        runningTasks.delete(taskId);
        updateTaskButton(taskId, false);
        alert(`❌ Network error: ${err.message}`);
        console.error('Network error:', err);
    });
}

function updateTaskButton(taskId, isRunning) {
    const btn = document.getElementById(`task-btn-${taskId}`);
    if (!btn) return;

    if (isRunning) {
        btn.textContent = 'Running...';
        btn.classList.add('btn-disabled');
        btn.disabled = true;
    } else {
        btn.textContent = 'Run';
        btn.classList.remove('btn-disabled');
        btn.disabled = false;
    }
}

function refreshLauncher() {
    loadLauncher();
}

// Load launcher on init
document.addEventListener('DOMContentLoaded', function() {
    // ... existing code ...
    loadLauncher();  // Add this
});

// ============================================================================
// Utility Functions
// ============================================================================

function formatUptime(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    
    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

// ============================================================================
// Admin Controls
// ============================================================================

function toggleControls() {
    const modal = document.getElementById('controls-modal');
    modal.style.display = modal.style.display === 'none' ? 'flex' : 'none';
}

function pauseAdmission() {
    fetch('/api/admin/pause', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            alert('Admission paused');
        })
        .catch(err => {
            alert('Error: ' + err);
        });
}

function resumeAdmission() {
    fetch('/api/admin/resume', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            alert('Admission resumed');
        })
        .catch(err => {
            alert('Error: ' + err);
        });
}

function killOperation() {
    const input = document.getElementById('kill-operation-input');
    const operationType = input.value.trim();
    
    if (!operationType) {
        alert('Please enter an operation type');
        return;
    }
    
    if (!confirm(`Kill all tokens of type "${operationType}"?`)) {
        return;
    }
    
    fetch('/api/admin/kill_operation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation_type: operationType })
    })
        .then(res => res.json())
        .then(data => {
            alert(`Killed ${data.killed} tokens`);
            input.value = '';
        })
        .catch(err => {
            alert('Error: ' + err);
        });
}

function drainPool() {
    if (!confirm('DRAIN POOL? This will kill all waiting tokens!')) {
        return;
    }
    
    fetch('/api/admin/drain', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            alert(`Drained ${data.drained} tokens`);
        })
        .catch(err => {
            alert('Error: ' + err);
        });
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    console.log('Dashboard initialized');
    initWebSocket();
    
    // Close modal when clicking outside
    const modal = document.getElementById('controls-modal');
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            toggleControls();
        }
    });
});
