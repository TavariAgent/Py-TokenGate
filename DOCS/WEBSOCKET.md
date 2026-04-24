# WebSocket Usage Guide

## Overview

TokenGate includes full WebSocket support to act as an **operational Control Plane** for your   
applications. It facilitates real-time communication between your client scripts and the backend   
server, allowing for dynamic task management, deep telemetry monitoring, and granular control   
over concurrent execution environments.

---

## Usage Guide

### 1. Setting Up the Server

Ensure that the host port is available, then start the TokenGate web server from your terminal:    

```bash
python launch_gui.py
```
*(Note: Adjust the path if running from outside the repository root).*

Once the server is running, open your browser and navigate to `http://localhost:5000` (or   
the appropriate IP address/hostname if hosting on a remote machine). 

*Behind the scenes, the server binds to your local network by dynamically deriving the hostname   
via `socket.gethostname()`.*

### 2. Preparing Your Scripts (Sinfully Easy Setup)

To enable WebSocket communication and DoS protection for your tasks, you only need **two   
lines of code**: initializing the global event bus, and adding the `@task_token_guard` decorator   
to your synchronous functions.

```python name=my_script.py
from operations_coordinator import get_global_coordinator
from token_system import task_token_guard

# Step 1: Initialize the TokenGate event bus 
# This instantly binds your script to the WebSocket control plane.
get_global_coordinator()

# Step 2: Decorate your synchronous functions
@task_token_guard(operation_type='string_ops', tags={'weight': 'light'})
def string_operation_task(task_data):
    # Your normal synchronous code goes here. 
    # TokenGate handles the threading and telemetry automatically!
    return "result"

# Step 3: Use a synchronous main() function. NEVER use async main() through WebSocket.
def main():
    # Execute your functions as you normally would.
    # No need for complex setup or teardown logic here—the server manages it.
    print("Running task...")
    string_operation_task("test_data")

if __name__ == "__main__":
    main()
```

### 3. Registering Tasks in the Launcher

To make your script executable directly from the TokenGate Web GUI, you must register it in the  
launcher configuration. 

Open `launcher_config.py` and add your script to the `REGISTERED_TASKS` dictionary:

```python name=launcher_config.py
REGISTERED_TASKS = {
    # Add your custom tasks here!
    'my_task': {
        'module': 'my_script',               # The Python file name (without .py)
        'function': 'main',                  # The entry point function to call
        'description': 'My custom task',     # Displayed in the GUI
        'category': 'Production',            # Groups tasks in the GUI
        'icon': '⚙️',                        # Visual identifier
        'enabled': True
    },
}
```

### 4. Running Your Tasks

You can run it directly from the Web GUI. Click the "Run" button next to your task in the task  
launcher, and it will execute with full WebSocket integration. You can monitor the task's   
progress and manage it through the "Controls".

![Task Launcher](/assets/task_launcher.png)

---

## Extended Usage

### Active Task Control

The WebSocket interface allows you to control the execution of tasks dynamically. From the   
dashboard, you can monitor execution times, view core affinity, and initiate administrative  
controls (Pause, Drain, Kill) on active task pools as well as per-task controls.

![Admin Dashboard](/assets/per_operation_controls.png)

> **Note:** Every administrative action triggers a real-time notification in the GUI to   
> confirm success or failure when managing live tokens.

![Notification Panel](/assets/notification.png)

### Native Desktop GUI Integration

Because TokenGate operates independently of the GIL and runs via background threads, it can   
act as the backend engine for "Thick Clients" (Native OS Desktop Apps). 

In full integration scenarios, launching a task from the WebSocket dashboard can initiate a   
native client-side user interface (like Tkinter or PyQt). The client UI remains smooth and   
responsive while TokenGate handles the heavy concurrent processing and telemetry in the background.  
*(See `demo_client_gui.py` for a live example).*
 
![Dual GUI](/assets/dual_gui.png)  

---

## Active DoS Defense (Auto-Blocking)

For heavy production workloads or public-facing integrations, it is highly recommended   
to enable TokenGate's automatic anomaly detection. This prevents system overload by   
automatically blocking operations that flood the event bus or exhibit dangerous execution   
patterns.

You can enable this by passing `auto_block_dangerous=True` when initializing your coordinator:

```python
# Enable active DoS defense at startup
get_global_coordinator(auto_block_dangerous=True)
```

### Further Reading

For a deeper understanding of the architecture and how to maximize its potential,  
see the following resources:  

- [concept.md](/DOCS/concept.md) — Deep dive into the architecture and design principles.
- [proof-of-concept.md](/DOCS/proof-of-concept.md) — Full architecture walkthrough with benchmarks.
- [quick-proof.md](/DOCS/quick-proof.md) — Focused proof of CPU, I/O, and mixed workloads.