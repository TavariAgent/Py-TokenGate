# TokenGate Beta Documentation

## Overview

Welcome to the TokenGate Beta documentation! This document provides an overview of the current state of the  
TokenGate project, including its features, limitations, and future plans. I also include a quick startup guide  
for both the WebSocket and regular operations. Please note that this is a beta version, and while it is  
stable and actively tested, edge cases and rough spots are expected at this stage.  


### Current Features

- **Token based concurrency:** The core feature of TokenGate is its token-based concurrency model, which  
    allows for efficient coordination of tasks across multiple threads.  
- **WebSocket support:** TokenGate includes support for WebSocket communication, enabling real-time  
    monitoring and control of tasks.
- ***DoS protection*:** The token system includes built-in DoS protection ("auto-blocking") to prevent overwhelming    
    the system with too many concurrent tasks.
- **Telemetry:** TokenGate provides telemetry data for tasks, allowing for monitoring and debugging of concurrent   
    operations with clarity.

### Limitations
- **Beta status:** As a beta project, TokenGate may have bugs or performance issues that have not yet been    
    identified or resolved.
- **Limited documentation:** The documentation is currently limited and may not cover all aspects of the    
    project in detail. I plan to expand the documentation as the project progresses.  
- **Performance under extreme load:** While TokenGate is designed to handle a high volume of tasks, its   
    performance under extreme load conditions needs long term analysis.  

### Future Plans
- **Improved documentation:** I plan to expand the documentation to provide more detailed information on how    
    to use TokenGate and its various features.
- **Performance optimizations:** I will continue to optimize the performance of TokenGate, especially under  
    high load conditions.
- **Additional features:** I am considering adding additional features such as more granular control over task  
    execution and support for distributed execution across multiple machines.

## Get Started

### Detailed startup guide:

#### Running the WebSocket server:
```bash
python launch_gui.py OR python -m your_package_root.launch_gui
```
*(Note: Adjust the path if running from outside the repository root).*

#### Using OperationsCoordinator without WebSocket:
```python
from operations_coordinator import OperationsCoordinator # Must accompany main()
from token_system import task_token_guard # The independent imported decorator for your functions

# How main functions should be structured when not using WebSocket.
def main():
    coordinator = OperationsCoordinator() # The key
    coordinator.start() # The ignition
    try: 
        # Normal main body
        pass
    finally:
        coordinator.stop() # The shutdown

if __name__ == "__main__":
    main()

# Setting up a method with the token decorator is the same regardless of WebSocket usage.
# CPU only 'weight' options: 'light', 'medium', 'heavy'
# CPU only example:
@task_token_guard(operation_type='string_ops', tags={'weight': 'light'})
def string_operation_task(task_data):
    # Simulate a task for threading
    return result

# Setting up an IO-centric method with the token decorator:
# IO writer counts for 'storage_speed':
# 'SLOW' (10 writes), 'MODERATE'(25 writes), 
# 'FAST' (50 writes), 'INSANE' (70 writes) <- CAUTION
@task_token_guard(operation_type='data_processing', tags={'weight': 'heavy', 'storage_speed': 'MODERATE'})
def data_processing_task(task_data):
    # Simulate a data processing task
    return result
```

#### Using OperationsCoordinator with WebSocket:
```python
from operations_coordinator import get_global_coordinator # Must accompany main()

# Get coordinator (GUI already started it)
get_global_coordinator() # One line at module-level to rule the whole WebSocket!

# No specific calls for main using the WebSocket
def main():
    try:
    # Main is free now!
    finally:
        pass

if __name__ == "__main__":
    main() # This gets used by the entry point in the WebSocket launcher to route 'func' (function)!!!

# Decorated functions will use task_token_guard and the WebSocket integration will   
# detect when the coordinated main is running to track task submissions.
```

#### How to register WebSocket tasks in the launcher:
```python
# Open launcher_config.py and add your task to the REGISTERED_TASKS dictionary:
REGISTERED_TASKS = {
    # Add your custom tasks here!
    'my_task': {
        'module': 'my_script',               # The Python file name (without .py)
        'function': 'main',                  # The entry point function to call
        'description': 'My custom task',     # Displayed in the GUI
        'category': 'Game server',           # Groups tasks in the GUI
        'icon': '⚙️',                        # Visual identifier
        'enabled': True
    },
}
```

#### What to look for in the WebSocket GUI

When you first set up your WebSocket environment and want to run a task, you should see the following at `localhost:5000`:
- **Task Launcher:** Your registered task should appear in the task launcher with its description and category.
![Task Launcher](/assets/task_launcher.png)



- **Controls:** When you run the task, there is controls where you can monitor and manage it (e.g., stop, restart).
![Admin Dashboard](/assets/per_operation_controls.png)  

- **Telemetry:** As the task runs, you should see telemetry data such as execution time, token usage,   
    and any relevant logs or outputs.
![TokenGate Dashboard — live run](/assets/dash_working.png)

## Conclusion

TokenGate is an active beta project that aims to provide a powerful and flexible concurrency management system   
using tokens. While it is currently functional, there are still areas for improvement and optimization. I encourage   
users to explore the project, provide feedback, and contribute to its development. 📦 🚀