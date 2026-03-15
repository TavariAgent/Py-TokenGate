# launcher_config.py
"""
Task Launcher Configuration

Add your scripts here to make them available in the GUI.
"""

REGISTERED_TASKS = {

    "gui_pressure_demo": {
        "module": "demo.gui_pressure_demo",
        "function": "main",
        "description": "Controlled CPU/IO pressure runner for GUI token testing",
        "category": "Demo",
        "icon": "🔥",
        "enabled": True,
    },

    "gui_pressure_short": {
        "module": "demo.gui_pressure_demo",
        "function": "short_main",
        "description": "Short burst token pressure test",
        "category": "Demo",
        "icon": "💡",
        "enabled": True,
    },

    # Add your custom tasks here!
    # 'my_task': {
    #     'module': 'my_module',
    #     'function': 'run_task',
    #     'description': 'My custom task description',
    #     'category': 'Production',
    #     'icon': '⚙️'
    # },
}

# Task categories (for organizing in GUI)
CATEGORIES = {
    'Testing': {'color': '#8b5cf6', 'order': 1},
    'Production': {'color': '#10b981', 'order': 2},
    'Demo': {'color': '#3b82f6', 'order': 3},
    'Maintenance': {'color': '#f59e0b', 'order': 4},
    'Custom': {'color': '#6366f1', 'order': 5}
}