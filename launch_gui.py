#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web GUI Launcher

Starts the Flask web dashboard for monitoring and controlling the threading engine.
"""

import sys
import os

from .gui import app as gui_app

if __name__ == '__main__':
    print("=" * 70)
    print("WEB GUI LAUNCHER")
    print("=" * 70)
    print()

    # Check if dependencies are installed
    try:
        import flask
        import flask_socketio
    except ImportError:
        print("ERROR: Flask dependencies not installed!")
        print()
        print("Please run:")
        print("  pip install -r gui/requirements.txt")
        print()
        sys.exit(1)

    # Start coordinator
    gui_app.start_coordinator()

    # Get local IP
    import socket

    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = "<unknown>"

    print(f"Dashboard accessible at:")
    print(f"  Local:   http://localhost:5000")
    print(f"  Network: http://{local_ip}:5000")
    print()
    print("Press Ctrl+C to stop")
    print()

    try:
        # Run Flask with SocketIO
        gui_app.socketio.run(
            gui_app.app,
            host='0.0.0.0',
            port=5000,
            debug=False,
            use_reloader=False
        )

    except KeyboardInterrupt:
        print("\n\nShutting down...")

    finally:
        # Cleanup
        gui_app.stop_coordinator()
        print("\nGoodbye!")