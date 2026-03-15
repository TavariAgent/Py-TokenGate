# TokenGate

Welcome to the TokenGate repository.

---

### What it is:

A small experimental system for routing decorated synchronous functions  
through a token-managed concurrency model. It is intended to operate as  
its own concurrency workflow rather than alongside normal threading patterns.  

### What it is not:

It is not presented as production code.

### Overview:

TokenGate is an exploration of token-managed concurrency: a   
concept for coordinating async orchestration with thread-backed   
work in a structured way.

This repository is **a proof of concept, not a finished product**.   
It is experimental, still evolving, and shared in the spirit of   
exploration.  

If you'd like the fuller overview, please start here:

- [Proof of Concept](./proof-of-concept.md)

 If anything here is useful, interesting, or sparks an   
 idea, that already makes this project worthwhile.

---

## How to Use (Two Versions, Two Decorators)
> ### Note: Do not attempt to decorate an async fucntion.
>
> #### *The token decorator uses asyncio, but the decorated function itself should be synchronous.* 

```python
#  -- Python 3.12 -- #
import asyncio
from operations_coordinator import OperationsCoordinator
from token_system import task_token_guard

# CPU only 'weight' options: 'light', 'medium', 'heavy'
# CPU only example:
@task_token_guard(operation_type='string_ops', tags={'weight': 'light'})
def string_operation_task(task_data):
    # Simulate a task for threading
    return result

# IO writer counts for 'storage_speed':
# 'SLOW' (10 writes), 'MODERATE'(25 writes), 
# 'FAST' (50 writes), 'INSANE' (70 writes) <- CAUTION
# CPU and IO combined example:
@task_token_guard(operation_type='data_processing', 
                  tags={'weight': 'heavy', 'storage_speed': 'MODERATE'})
def data_processing_task(task_data):
    # Simulate a data processing task
    return result

# Usage #1 (optimal - most inclusive):
async def main():
    coordinator = OperationsCoordinator()
    coordinator.start()
    try: 
    # Normal main body
    finally:
        coordinator.stop()

if __name__ == "__main__":
    asyncio.run(main())

# Usage #2 (simpler - less inclusive):
def main():
    coordinator = OperationsCoordinator()
    coordinator.start()
    try: 
    # Normal main body
    finally:
        coordinator.stop()
        
if __name__ == "__main__":
    main()
```

---

## Project status

TokenGate is an active proof of concept.

Current focus:
- Refine the core concurrency model
- Improve system operability and confirm WebSocket behavior
- Test the decorator/coordinator workflow
- Gather feedback on API clarity and usability

This is a base architecture, not a finished product.