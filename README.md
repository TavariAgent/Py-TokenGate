# TokenGate (Python 3.12*)

Welcome to the TokenGate repository.

(ProcessPoolExecutor support added. See "Explicit ThreadPool and ProcessPool support".)

> Read BETA.md for the quickest entry point and overview of the TokenGate.  
> If you're not sure about this and want to see it in action, go to SETUP.md   
> for a quick demo and walkthrough under varous loads.

---

#### NOTE: If your functions seem to stop running, it usually means you require a "hash_policy".

The unhashable checker flags various unhashable types, you cannot make a contract with those types   
without explicitly stating the hash policy, this is for clarity and user application safety.  

The system will attempt to convert unhashable types to hashable to keep domain locks on more various   
operations, if it finds something that can't be hashed it will not be operated on by the domain hashing   
algorithm.

### What this is:

A small experimental system for routing decorated synchronous functions through a token-managed   
concurrency model. It is intended to operate as its own concurrency workflow rather than alongside   
normal threading patterns.

### What this is not:

It is not presented as a production ready product. (Use at your own discretion)

### Overview:

TokenGate is an exploration of token-managed concurrency: a concept for coordinating async   
orchestration with thread-backed work in a structured way.

This repository is **a proof of concept, not a finished product**. It is experimental, still evolving,   
and shared in the spirit of exploration.  

If you'd like the fuller overview, please start here:  

(Note: Some sections are still being rewritten, but the core concepts remain.)

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

### NEW! Explicit TheadPool and ProcessPool support

## Choosing Your Executor Pool

TokenGate routes decorated tasks to either a `ThreadPoolExecutor` or a  
`ProcessPoolExecutor` based on tags you set in `@task_token_guard`. The  
default is always the thread pool — you opt into the process pool  
explicitly.

---

### Thread Pool (default)

**Best for:**
- Any operation that touches IO — file reads/writes, database queries,  
  network calls, asset loading  
- Operations using the `storage_speed` tag (this signals IO automatically)  
- Short to medium CPU tasks where spawn overhead would outweigh the gain  
- Operations that capture external state, use locks, or hold references to   
  objects that can't be pickled  
  
**How to use:**  

```python
# Default — no tag needed
@task_token_guard(
    operation_type='write_file', 
    tags={'weight': 'heavy', 'storage_speed': 'FAST'}
)
def write(data): 
    ...

# Or explicitly
@task_token_guard(
    operation_type='operation', 
    tags={'weight': 'medium', 'process_pool': True}
)
def process(x): 
    ...
```

---

### Process Pool

**Best for:**
- Long-running, purely CPU-bound work with no IO involvement
- Operations with tight numeric loops, matrix operations, recursive algorithms
- Tasks whose inputs and outputs are simple, serialisable values

**How to use:**

```python
@task_token_guard(
    operation_type='cpu_intensive',
    tags={'weight': 'heavy', 'process_pool': True} # Explicit opt-in to process pool
)
def heavy_compute(n): 
    ...
```

> #### *Note: Complex operations using a process pool might perform better in some cases.*


---

### What to watch out for

**Pickling errors**   
Everything crossing the process boundary must be picklable — the  
function, its arguments, and its return value. Common causes of failure:  

- Lambdas and functions defined inside other functions  
- Objects holding locks, file handles, or socket connections
- Anything referencing asyncio state
- NumPy arrays with object dtypes, or custom classes without `__reduce__`

A `PicklingError` at call time means the task belongs in the thread pool.

**Spawn overhead**  
Process pool has a fixed startup cost of roughly 1–5ms per task dispatch.    
For fast operations this cost exceeds the actual work.
 

**Conflicting tags**    
Setting both `storage_speed` and `process_pool: True` on the same  
operation is a misconfiguration. TokenGate will warn and fall back to  
the thread pool, since IO presence always takes priority.  

### NEW! Data locality tags for "Sticky anchors" & "Lead tokens"

```python

# A "sticky_anchor" is for tokens which must return on the same call chain 
# more than one time or in identical form. This uses the "local_i" (local 
# worker index) to route those back to their same domains.
@task_token_guard(
    operation_type='my_op',
    tags={'weight': 'medium', 'sticky_anchor': 'op_token'}, 
)
def my_operation(n: int) -> int:
    ...

# "external_calls" is a declaration for tokens which have child operations. 
# Only the leads need to be marked with the tag, they may also declare how 
# much complexity each hash should be. The system will guard you if you try 
# to hash something un-hashable, just know that it's fairly secure as is. 
# If you aren't sure about the hashability read prints and rethink the 
# aprroach. Here's details on the policies (hash collisions are benign!):
"""        Policy controls digest algorithm and output length:

            FULL    — SHA-256 full 64-char hex (default).
            SHORT   — SHA-256 truncated to 16 chars (64-bit space).
            FAST    — BLAKE2s 8-byte digest → 16-char hex (64-bit space,
                      lower compute cost than SHA-256).
            MINIMAL — SHA-256 truncated to 8 chars (32-bit space).
                      Collisions are benign but shift load distribution —
                      see module docstring for full collision semantics.
                      
Fingerprint semantics
=====================
This produces ROUTING FINGERPRINTS, not equality witnesses.

    Array-like   →  (lib_hint, shape, dtype_str)
    Container    →  recursively frozen equivalent
    GPU object   →  (type_name, id)          [stable within session]
    Dataclass    →  (type_name, id)          [identity routing]
    Unknown      →  (type_name, repr[:256])  [best-effort stability]

Public API
==========
    make_hashable(obj)        →  Hashable
    fast_make_hashable(obj)   →  Hashable  (builtins + stdlib only)
    safe_args_key(args)       →  tuple[Hashable, ...]
    is_hashable(obj)          →  bool
    HashPolicy                →  Enum (NONE | FAST | STANDARD | FULL)
    DigestPolicy              →  Enum (FULL | SHORT | FAST | MINIMAL)
"""
@task_token_guard(
    operation_type='lead',
    tags={'weight': 'medium',
          # "hash_policy" determines token routing checks.
          'hash_policy': HashPolicy.FAST, # Conditional (default is STANDARD)
          'digest_policy': DigestPolicy.FAST, # Optional (default is FULL)
          'external_calls': ['child']},
)
def lead_operation(n: int) -> list:
    return [child_op(n + i) for i in range(4)]
# Hash policy should be stated when hashing is involved in the token. Means 
# contracts are clear about how hashes have to be checked.

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