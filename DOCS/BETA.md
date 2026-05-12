# TokenGate Beta Documentation

## Overview

Welcome to the TokenGate Beta documentation!   

This document provides an overview of the current state of TokenGate.

### Current Features

- **Token based concurrency:** The core feature of TokenGate is its token-based concurrency model, which  
    allows for efficient coordination of tasks across multiple threads.
- **WebSocket support:** TokenGate includes support for WebSocket communication, enabling real-time  
    monitoring and control of tasks.
- **DoS protection:** The token system includes built-in DoS protection ("auto-blocking") to prevent overwhelming    
    the system with too many "failed" concurrent tasks. (Tasks which do not return their result are "failed".)
- **Telemetry:** TokenGate provides telemetry data for tasks, allowing for monitoring and debugging of concurrent   
    operations with clarity.
- **Flexible API:** The API is designed to be flexible and easy to use, allowing developers to quickly integrate  
    TokenGate into their applications with minimal setup.
- **Token safety:** The system is designed to prevent data loss and ensure that tokens are properly managed  
    based on hashing as well as controlled token locality.

### Limitations
- **Beta status:** As a beta project, TokenGate may have bugs or performance issues that have not yet been    
    identified or resolved.
- **Limited documentation:** The documentation is currently limited and does not cover all aspects of the    
    project. I plan to expand the documentation as the project progresses.  
- **Performance under extreme load:** While TokenGate is designed to handle a high volume of tasks, its   
    performance under extreme load conditions needs long term analysis.

## Get Started

### Using OperationsCoordinator event bus *without* WebSocket:
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
# 'FAST' (50 writes), 'INSANE' (70 writes) <- CAUTION only top-end SSDs will handle this.
@task_token_guard(
    operation_type='data_processing', 
    tags={'weight': 'heavy', 'storage_speed': 'MODERATE'}
)
def data_processing_task(task_data):
    # Simulate a data processing task
    return result


# -----------------------------------NEW*-----------------------------------
# As of v0.2.2.0-beta Awaiting tokens is supported, allowing for more complex
# orchestration patterns. "result = await token" works to await a single token.

# Or a full batch can be awaited with asyncio.gather:
async def main():
    coordinator = OperationsCoordinator()
    coordinator.start()
    try:
        tokens = [my_task(i) for i in range(64)]
        results = await asyncio.gather(*tokens)
    finally:
        coordinator.stop()

asyncio.run(main())

# -----------------------------------NEW------------------------------------
# A "sticky_anchor" tag can be added to any decorator to give the sticky 
# key an explicit name, independent of operation type. Sticky tokens are 
# pinned to the same core domain and create a path for related operations 
# to follow the same route, ensuring data locality and consistent performance
# even when the operations do not require this behavior to work.

# This happens automatically when the system interprets tokens with matching
# (operation_type, args) keys.
@task_token_guard(
    operation_type="my_op", # Use a unique operation type for clarity.
    tags={"weight": "medium", "sticky_anchor": "op_token"}, 
    # The 'sticky_anchor' tag gives this token a specific known moniker.
    # Good naming conventions for the sticky_anchor value can assist debugging.
)
def my_operation(n: int) -> int:
    ...

# -----------------------------------NEW------------------------------------
# "Lead tokens" — those decorated with 'external_calls' generate a SHA-256 seed from their 
# token ID and call list. That seed is pinned to a core domain. Any token spawned during 
# the lead's execution inherits the seed and gets routed to the same core automatically. 
# The domain releases when the lead and all of its children have completed. This is a 
# production ready feature that provides deterministic routing, prevents data races, 
# and measurably better behaviour under saturated load conditions for complex call chains.
@task_token_guard(
    operation_type="lead_op",
    tags={"weight": "medium", "external_calls": ["child_op"]},
)
def lead_operation(n: int) -> list:
    return [child_op(n + i) for i in range(4)]
```

### Running the WebSocket server:

```bash
python launch_gui.py OR python -m your_package_root.launch_gui
```
*(Note: Adjust the path if running from outside the repository root).*


#### Using OperationsCoordinator *with* WebSocket:
```python
from operations_coordinator import get_global_coordinator # Must accompany main()

# Get coordinator (GUI already started it)
get_global_coordinator() # One line at module-level to rule the whole WebSocket!

# No specific calls for main using the WebSocket.
def main():
    # Main is free now!
    try:
        pass
    finally:
        pass

if __name__ == "__main__":
    main() # This gets used by the entry point in the WebSocket launcher to route 'func'!

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

TokenGate is an active beta project that aims to provide a powerful and flexible production ready 
concurrency management system using tokens. While it is currently functional, there are still areas for 
improvement and optimization. I encourage users to explore the project, provide feedback, and contribute 
to its development. 📦 🚀

To quickly reach out go here and drop a review in the feedback section: [Tavari](https://www.tavari.online)  

I see a couple hundred of you out there, if this has been fun, or useful or even just something you want to   
see grow, please leave a star on GitHub, it helps me spread the freedom of TokenGate