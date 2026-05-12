# TokenGate (Python 3.12*)

Welcome to the TokenGate repository. 

### (Unhashable operations fixed as of v0.3.3.3-beta)

> Read BETA.md for the quickest entry point and overview of the TokenGate.  
> If you're not sure about the this and want to see it in action, go to SETUP.md   
> for a quick demo and walkthrough under varous loads.

---

### What it is:

A small experimental system for routing decorated synchronous functions through a token-managed   
concurrency model. It is intended to operate as its own concurrency workflow rather than alongside   
normal threading patterns.

### What it is not:

It is not presented as production code.

### Overview:

TokenGate is an exploration of token-managed concurrency: a concept for coordinating async   
orchestration with thread-backed work in a structured way.

This repository is **a proof of concept, not a finished product**. It is experimental, still evolving,   
and shared in the spirit of exploration.  

If you'd like the fuller overview, please start here:

- [Proof of Concept](./DOCS/proof-of-concept.md)

 If anything here is useful, interesting, or sparks an idea, that already makes this project worthwhile.

---

## How to Use (Two Versions, Two Decorators)
> ### Note: Do not attempt to decorate an async function.
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
@task_token_guard(
 operation_type='data_processing', 
 tags={'weight': 'heavy', 'storage_speed': 'MODERATE'}
)
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

### NEW! Data locality tags for "Sticky anchors" & "Lead tokens"

```python

# "Sticky anchors" give you control over the data locality of your operations.  
# By using consistent sticky anchors, you can ensure that identical operations   
# are routed to the same core, which can improve cache performance and reduce  
# latency for operations that share data or have similar access patterns. For   
# instance: Any operation that produces multiple returns during fucntion call   
# will also produce multiple tokens, and if those tokens are not pinned to the   
# same core they will lose the benefits of data locality.
@task_token_guard(
    operation_type="my_op",
    tags={"weight": "medium", "sticky_anchor": "op_token"}, 
)
def my_operation(n: int) -> int:
    ...

# "Lead tokens", those decorated with `external_calls` generate a SHA-256 seed   
# from their token ID and call list. That seed is pinned to a core domain. Any   
# token spawned during the lead's execution inherits the seed and is routed to   
# the same core automatically. The domain releases when the lead and all of its   
# children have completed. This is a powerful way to ensure that an entire call   
# chain stays together on the same core, which can be critical for performance   
# when the operations are related and share data. For instance: If you have a   
# function that spawns multiple child operations, you can use `external_calls`   
# to ensure that all of those operations are executed on the same core, which   
# can improve performance by keeping related data in the same cache and reducing   
# cross-core communication overhead.
@task_token_guard(
    operation_type="lead_op",
    tags={"weight": "medium", "external_calls": ["child_op"]},
)
def lead_operation(n: int) -> list:
    return [child_op(n + i) for i in range(4)]

# !!! CAREFUL !!! Don't mix `sticky_anchor` and `external_calls` on the same token.   
# They are separate systems that both control data locality, but they do so in   
# different ways and are not designed to be used together on the same token. Using   
# them together could lead to unexpected behavior and routing issues. Choose one  
# approach based on your specific needs for data locality and routing control.
```

### Awaiting 

The system now supports correct use of `__await__` which has enabled a more fine tuned   
control of the event bus.  


```python
results  = await asyncio.gather(*tokens, return_exceptions=True)
```

---

## Project status

TokenGate is an active proof of concept and beta.

Current focus:
- Update DOCS to reflect recent changes and clarify usage
- Improve system operability and confirm WebSocket behavior
- Gather feedback on API clarity and usability

This is a beta system, it's still improving, if there's any issues, don't hesitate to report them on GitHub.