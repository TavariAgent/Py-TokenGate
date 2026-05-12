# Quick-Proof: TokenGate Architecture

---

#### Quick Links

- [Proof 1: CPU-Based Operation Scheduling](#proof-1-cpu-based-operation-scheduling)
- [Proof 2: I/O-Based Workloads](#proof-2-io-based-workloads)
- [Proof 3: Mixed Workload Coordination](#proof-3-mixed-workload-coordination)
- [Proof 4: Observability and Control](#proof-4-observability-and-control)
- [Conclusion](#conclusion)

## Scope
This example demonstrates the base of TokenGate and what it does without  
Guard House, launcher/web flow, or token interception patterns. The goal   
is to show that ordinary Python functions can be scheduled as token-managed   
operations, that CPU-centric and I/O-centric workloads can be coordinated   
together, and that execution remains observable.

### Claims
1. Ordinary Python functions can be scheduled as token-managed operations.
2. TokenGate displays use of CPU and I/O workloads under one execution model.
3. Execution remains observable through routing, worker, and shutdown logs.  


---

## Proof 1: CPU-Based Operation Scheduling


<details>
<summary> CPU-Based Operation Scheduling  

> TokenGate uses natural queue-bounded backpressure. The admission  
> layer does not impose artificial pacing, but execution remains   
> contained by bounded worker mailboxes, and async submission flow.  
> 
> Max mail length should be tested to find ideal limits for your  
> use case. The architecture is designed to be flexible, allowing for  
> adjustments based on specific workload requirements and system   
> capabilities.

> **Notably:** *When failure occurs tokens are re-distributed in FIFO and are   
> triggered to re-start interleaved with the current task queue.*

```python
# Optional: cap mailbox length to prevent runaway memory
self.MAILBOX_MAX = 425
```
</summary>

> Objective: Show scheduled operations as token-managed tasks

```python
# Examples of operations that can be scheduled as a token-managed task.
from token_system import task_token_guard

@task_token_guard(operation_type='simple_loop', tags={'weight': 'light'})
def simple_operation(n):
    """Fast: Basic operation with accumulation."""
    total = 0
    for i in range(n):
        total += i
    return total

@task_token_guard(operation_type='nested_loops', tags={'weight': 'medium'})
def complex_operation(dimension):
    """Slower: Nested loops with matrix-like structure."""
    matrix = []
    for i in range(dimension):
        row = []
        for j in range(dimension):
            row.append(i * j)
        matrix.append(row)
    return sum(sum(row) for row in matrix)
    
@task_token_guard(operation_type='cpu_intensive', tags={'weight': 'heavy'})
def heavy_operation(iterations):
    """Slow: CPU-intensive calculation."""
    result = 0
    for i in range(iterations):
        result += sum(j ** 2 for j in range(100))
    return result
```

> Setup: Definition of CPU functions and scheduled as token-managed tasks.

```python
from operations_coordinator import OperationsCoordinator, task_token_guard

@task_token_guard(operation_type='fibonacci', tags={'weight': 'heavy'})
def fibonacci_operation(n):
    """Iterative fibonacci scheduled as a token-managed task."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

def main():
    print("[DEMO] Starting demo...")
    coordinator = OperationsCoordinator()
    coordinator.start()
    try:
        args = [100, 200, 300, 150, 250, 180, 220, 280]
        tokens = [fibonacci_operation(n) for n in args]
        results = [token.get(timeout=30) for token in tokens]
        print(f"Results: {results}")
    finally:
        coordinator.stop()
        print("[DEMO] Demo stopped.")

if __name__ == "__main__":
    main()
```
> Code Example: CPU-centric code using the architecture
> > Rule of thumb:   
> "heavy" = core 1-8,   
> "medium" = core 2-8,   
> "light" = core 3-8,   
> (if you have 8 cores)

```python
@task_token_guard(operation_type='normal_operation', tags={'weight': 'light'})
def normal_processing(items: List[int]) -> int:
    """
    Normal processing function that simulates a CPU-based operation.  
     - Performs a simple computation (summing squares) to simulate work.
     - Returns the total sum of squares.
     - Tagged as 'light' weight for scheduling purposes.
     - The function is decorated with @task_token_guard to ensure it is managed as a token
    """

    # Simple processing
    total = sum(x ** 2 for x in items)
    return total
```
> Results: Example prints from successful scheduling   
> and execution of CPU tasks

```
  ── [NORMAL] Stats at 709s ──
     Submitted : 5888
     Resolved  : 5888  (100.0%)
     Discarded : 0  (0.0%)
     Failed    : 0
     Latency   : p50=0.309s  p95=1.407s  max=1.890s
     
  ── [NORMAL] Stats at 731s ──
     Submitted : 6064
     Resolved  : 6064  (100.0%)
     Discarded : 0  (0.0%)
     Failed    : 0
     Latency   : p50=0.137s  p95=1.098s  max=1.709s
     
  ── [IDLE] Stats at 756s ──
     Submitted : 6208
     Resolved  : 6208  (100.0%)
     Discarded : 0  (0.0%)
     Failed    : 0
     Latency   : p50=0.116s  p95=0.300s  max=0.561s
     
  ── [IDLE] Stats at 777s ──
     Submitted : 6240
     Resolved  : 6240  (100.0%)
     Discarded : 0  (0.0%)
     Failed    : 0
     Latency   : p50=0.109s  p95=0.300s  max=0.561s
```
> Conclusion: The runtime sustained long-duration scheduling of   
> CPU-heavy operations with 100% resolution, zero failures, and   
> bounded latency across idle, normal, and surge phases.

```
======================================================
ENDURANCE RUN COMPLETE
======================================================
  Total wall-clock: 901.9s

  ── [FINAL] Stats at 902s ──
     Submitted : 7520
     Resolved  : 7520  (100.0%)
     Discarded : 0  (0.0%)
     Failed    : 0
     Latency   : p50=0.202s  p95=1.421s  max=2.016s

  Tasks per phase:
    IDLE    : 208 submitted
    NORMAL  : 3216 submitted
    SURGE   : 4096 submitted
```

</details>

[↑ Top](#scope)

---

## Proof 2: I/O-Based Workloads

<details>
<summary> I/O-Based Workloads  

> TokenGate is asynchronous and thread-based, it uses async to manage token delivery and   
> packaging while the threads operate a pinned worker queue. Each worker monitors a mailbox  
> with a specific index, and the index directs tokens to the appropriate worker based on tags.

```python
@task_token_guard(
    operation_type='write_json_fast', 
    tags={'weight': 'light', 'storage_speed': 'FAST'}
)
def write_json_fast(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "keys": len(payload),
    }

@task_token_guard(
    operation_type='append_log_slow', 
    tags={'weight': 'heavy', 'storage_speed': 'SLOW'}
)
def append_log_slow(path, message):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    return {
        "path": path,
        "chars": len(message),
    }

@task_token_guard(
    operation_type='write_blob_moderate', 
    tags={'weight': 'medium', 'storage_speed': 'MODERATE'}
)
def write_blob_moderate(path, size_kb):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    blob = b"x" * (size_kb * 1024)
    with open(path, "wb") as f:
        f.write(blob)
    return {
        "path": path,
        "bytes": len(blob),
    }
```
</summary>



> Objective: Coordinate I/O-centric workloads.

```python
@task_token_guard(operation_type='json_fast',
    tags={'weight': 'medium',  # Core routing
        'storage_speed': 'FAST'  # Storage throttling
    })# The decorator can activate on synchronous functions only.
def generate_medium_json(index: int) -> dict:
    """Generate medium-sized JSON (~50KB)."""
    return {
        'id': index,
        'timestamp': time.time(),
        'records': [
            {
                'id': i,
                'value': random.random(),
                'label': f'record_{i}',
                'tags': [f'tag_{j}' for j in range(5)],
                'nested': {
                    'x': random.randint(0, 100),
                    'y': random.randint(0, 100),
                    'data': [random.random() for _ in range(10)]
                }
            }
            for i in range(100)
        ],
        'metadata': {
            'version': '1.0',
            'type': 'medium',
            'generator': 'json_medium',
            'stats': {
                'total_records': 100,
                'created_at': time.time()
            }
        }
    }

# Calling this would still activate the decorator.
def create_json_file(index: int) -> dict:
    """
    Create a single JSON file with automatic storage throttling.

    This demonstrates the storage_speed tier system:
    - Tagged with 'FAST' tier (50 concurrent writes for NVMe)
    - No manual semaphore management needed
    """
    # Create a directory if needed
    _dir = Path('jsons')
    _dir.mkdir(exist_ok=True)

    # Generate data
    data = generate_medium_json(index)

    # Write file - throttling happens automatically!
    timestamp = int(time.time() * 1000)
    filename = _dir / f'json_fast_{timestamp}_{index:04d}.json'

    start = time.time()
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

    duration = time.time() - start
    file_size = filename.stat().st_size

    if index % 100 == 0:
        print(f"[JSON-{index:04d}] Created {file_size:,} bytes in {duration:.3f}s")

    return {
        'index': index,
        'filename': str(filename),
        'size_bytes': file_size,
        'write_time': duration
    }
```
> Setup: Definitions of a basic IO compatible function. (simple, no file output)

```python
import time
from typing import List
from operations_coordinator import OperationsCoordinator, task_token_guard

@task_token_guard(operation_type='data_transform_medium', tags={'weight': 'medium'})
def transform_dataset(data: List[int], operation: str, index: int) -> List[int]:
    """
    Medium data transformation - balanced workload.

    This will route to Core 2+ (never touches Core 1).
    """
    print(f"[MEDIUM-{index}] Transforming dataset ({len(data)} items, op: {operation})...")

    if operation == 'square':
        result = [x ** 2 for x in data]
    elif operation == 'fibonacci':
        result = []
        for x in data:
            a, b = 0, 1
            for _ in range(x):
                a, b = b, a + b
            result.append(a)
    else:
        result = [x * 2 for x in data]
        
    return result

def main():
    print("[DEMO] Starting demo...")
    coordinator = OperationsCoordinator()
    coordinator.start()
    try:
        transform_dataset(operation='square', data=list(range(200)), index=10)
        print("[DEMO] Dataset transformed successfully.")
    finally:
        coordinator.stop()
        print("[DEMO] Demo stopped.")

if __name__ == "__main__":
    main()
```
> Code Example: I/O-based code with storage throttling and core routing based on tags

```python
import time
import random
from operations_coordinator import task_token_guard

@task_token_guard(
    operation_type='json_generation_heavy', 
    tags={'weight': 'heavy', 'storage_speed': 'MODERATE'}
)
def generate_complex_json(depth: int, index: int) -> dict:
    """
    Heavy JSON generation - CPU intensive.

    This will route to Core 1 (protected core for heavy work).
    """
    print(f"[HEAVY-{index}] Generating complex nested JSON (depth: {depth})...")

    def create_nested(current_depth):
        if current_depth <= 0:
            return {"value": random.randint(1, 1000)}

        return {
            f"level_{current_depth}": {
                "data": [create_nested(current_depth - 1) for _ in range(3)],
                "metadata": {
                    "timestamp": time.time(),
                    "depth": current_depth,
                    "random": random.random()
                }
            }
        }

    result = create_nested(depth)
    return result
```
> Results: Example logs showing successful execution of   
> I/O tasks
```
[WORKER_16_CORE_6] ✓ Completed write_json_fast_1773231605448885800
[DEBUG] Recorded: write_json_fast_1773231605448885800
[WORKER_1_CORE_1] ✓ Completed append_log_slow_1773231605456579200
[DEBUG] Recorded: append_log_slow_1773231605456579200
[WORKER_9_CORE_4] ✓ Completed write_blob_moderate_1773231605461103700
[DEBUG] Recorded: write_blob_moderate_1773231605461103700
```
Conclusion — I/O-centric tasks were scheduled and executed successfully   
under the demonstrated routing and storage-throttling rules.

```
── I/O-BOUND PROOF ──────────────────────────────────────────────────
  Output directory: C:\Users
  
[STORAGE_THROTTLE] Initialized with speed tiers:
  SLOW: 10 concurrent I/O
  MODERATE: 25 concurrent I/O
  FAST: 50 concurrent I/O
  INSANE: 70 concurrent I/O

Submitting 10 tasks → JSON writes
10/10 resolved  |  0 failed
 ✓ JSON writes

Submitting 10 tasks → Log appends
10/10 resolved  |  0 failed
✓ Log appends

Submitting 6 tasks → Blob writes
6/6 resolved  |  0 failed
✓ Blob writes
✓ I/O proof complete.
```
</details>

[↑ Top](#scope)

---

## Proof 3: Mixed Workload Coordination

<details>
<summary> Mixed Workload Coordination  

> TokenGate is an example of bounded asynchronous concurrent
> execution.
  
Admission and routing occur through the async event bus, while execution is   
coordinated through worker queues, lock-managed state transitions, and a   
layered routing index.
  
Routing Index: The routing index applies tag-based core selection, storage-speed   
throttling for I/O tasks, and queue-order handling within each worker domain.  

> If you rely on a static time for token delivery this must be defined locally   
> via cadence, timesteps or similar.

```terminaloutput
======================================================================
MIXED ORCHESTRATOR COMPLETE
======================================================================
  Total wall-clock : 60.6s
  Waves            : 120
  Submitted        : 1789
  Resolved         : 1789  (100.0%)
  Failed           : 0
  Latency p50      : 0.018s
  Latency p95      : 0.036s
  Latency max      : 0.053s

  Operations dispatched:
    heavy_operation                       180  ████████████████████████████████████
    fibonacci_operation                   179  ███████████████████████████████████
    trivial_operation                     178  ███████████████████████████████████
    complex_operation                     176  ███████████████████████████████████
    write_blob_moderate                   169  █████████████████████████████████
    string_operation                      163  ████████████████████████████████
    append_log_slow                       150  ██████████████████████████████
    write_json_fast                       150  ██████████████████████████████
    moderate_operation                    149  █████████████████████████████
    prime_operation                       148  █████████████████████████████
    simple_operation                      147  █████████████████████████████
```
</summary>

> Objective: Coordinate CPU and I/O workloads together.

```python
@task_token_guard(operation_type='simple_function', tags={'weight': 'medium'})
def normal_processing(items: List[int], index: int):
    """
    Normal processing.
    """
    
    # Simple processing
    total = sum(x ** 2 for x in items)

    return total

# Storage speed tiers allow you to classify I/O operations   
# based on the expected performance characteristics of hardware.   
# Careful not to exceed the concurrency limits of your   
# storage medium, or you may trigger a function retry  
# or task failure.

#Storage Tiers: 
# (Naming is meant to be intuitively made to reflect the   
# implications of assigning the correct concurrent storage   
# limits, and to distinctly separate the logic since they are   
# for different systems. I recommend using one storage speed.)

# Tier 1: SLOW (10 concurrent),   
# Tier 2: MODERATE (25 concurrent),
# Tier 3: FAST (50 concurrent),   
# Tier 4: INSANE (70 concurrent)
@task_token_guard(operation_type='json_medium', tags={'weight': 'medium', 'storage_speed': 'MODERATE'})
def generate_medium_json(index: int) -> dict:
    """Generate medium-sized JSON (~50KB)."""
    return {
        'id': index,
        'timestamp': time.time(),
        'records': [
            {
                'id': i,
                'value': random.random(),
                'label': f'record_{i}',
                'tags': [f'tag_{j}' for j in range(5)],
                'nested': {
                    'x': random.randint(0, 100),
                    'y': random.randint(0, 100),
                    'data': [random.random() for _ in range(10)]
                }
            }
            for i in range(100)
        ],
        'metadata': {
            'version': '1.0',
            'type': 'medium',
            'generator': 'json_medium',
            'stats': {
                'total_records': 100,
                'created_at': time.time()
            }
        }
    }
```
> Setup: Both CPU and I/O functions can mix and operate concurrently.

```python
import os
import tempfile
from operations_coordinator import OperationsCoordinator, task_token_guard

@task_token_guard(operation_type='prime_check', tags={'weight': 'medium'})
def prime_operation(n):
    """Check if the number is prime (moderate complexity)."""
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True

@task_token_guard(
    operation_type='append_log_slow', 
    tags={'weight': 'heavy', 'storage_speed': 'SLOW'}
)
def append_log_slow(path, message):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    return {"path": path, "chars": len(message)}

def main():
    print("[DEMO] Starting demo...")
    coordinator = OperationsCoordinator()
    coordinator.start()
    try:
        base_dir = tempfile.mkdtemp(prefix="tokengate_mixed_")
        log_path = os.path.join(base_dir, "logs", "out.log")

        # Submit IO tasks
        log_tokens = [
            append_log_slow(log_path, f"entry {i}")
            for i in range(8)
        ]

        # Submit CPU tasks
        primes_to_check = [97, 101, 103, 107, 109, 113, 127, 131]
        prime_tokens = [prime_operation(n) for n in primes_to_check]

        # Resolve all
        log_results = [t.get(timeout=30) for t in log_tokens]
        prime_results = [t.get(timeout=30) for t in prime_tokens]

        print(f"  Log results  : {log_results[:3]}")
        print(f"  Prime results: {prime_results}")
        print("[DEMO] Mixed load completed.")

    finally:
        coordinator.stop()
        print("[DEMO] Demo stopped.")

if __name__ == "__main__":
    main()
```

> Even in a chain of dependent tasks, all decorated steps tasks   
> can complete naturally. The architecture will route each step  
> according to its tags, allowing for efficient execution without   
> manual concurrency management.

```python
@task_token_guard(operation_type='chain_seed', tags={'weight': 'light'})
def chain_seed(value: int):
    """
    Step 0: Produce an initial value.
    Entry point of a chain. Returns a transformed seed for the next step.
    """
    return value * 3 + 7

@task_token_guard(operation_type='chain_filter', tags={'weight': 'light'})
def chain_filter(value: int):
    """
    Step 1: Conditionally transform the incoming value.
    Demonstrates that chain steps can apply logic, not just pass data through.
    """
    if value % 2 == 0:
        return value // 2
    return value * 2 + 1

@task_token_guard(operation_type='chain_accumulate', tags={'weight': 'medium'})
def chain_accumulate(value: int):
    """
    Step 2: Expand the value into a sum across a range.
    Adds moderate CPU work mid-chain to demonstrate that chaining
    does not require tasks to be trivially fast.
    """
    return sum(i * value for i in range(1, 51))

@task_token_guard(operation_type='chain_reduce', tags={'weight': 'light'})
def chain_reduce(value: int):
    """
    Step 3: Reduce back to a compact form.
    Final transformation before the chain terminates.
    """
    return value % 9973  # mod a prime to keep numbers readable

@task_token_guard(operation_type='chain_finalize', tags={'weight': 'light'})
def chain_finalize(value: int, label: str = "result"):
    """
    Step 4: Annotate and return the final chain output.
    Demonstrates that chain steps can accept auxiliary arguments
    alongside their dependency-resolved input.
    """
    return {label: value, 'parity': 'even' if value % 2 == 0 else 'odd'}
```
> Results: Execution of mixed workloads was successful.

```
# Example output demonstrating successful execution of high   
# variability workloads using 1.0s - 0.1s intervals per batch,  
# with appropriate routing and no blocking. (10-20 tasks per   
# batch for high variance.)
======================================================================
MIXED ORCHESTRATOR COMPLETE (bars removed due to length)
======================================================================
  Total wall-clock : 900.1s
  Waves            : 1731
  Submitted        : 26189
  Resolved         : 26189  (100.0%)
  Failed           : 0
  Latency p50      : 0.016s
  Latency p95      : 0.036s
  Latency max      : 0.072s
```

</details>

[↑ Top](#scope)

---

## Proof 4: Observability and Control

<details>
<summary> Observability and Control

TokenGate is designed to be observable through routing logs,  
worker lifecycle logs, initialization prints, and shutdown traces.  
The system includes logging and monitoring that allow testers to  
observe task execution.

```terminaloutput
CPU Topology Detected:
  Physical cores: 8
  Logical cores: 8
  SMT enabled: False
  SMT ratio: 1.0x
  Available cores: 8

Recommended worker counts:
  Light workload (I/O bound): 32
  Medium workload (mixed): 24
  Heavy workload (CPU bound): 16
[COORD]      Creating foundation components...
[OVERFLOW]   Initialized
[OVERFLOW]   Base budget: 65 MB
[OVERFLOW]   Retry policies loaded for all complexity levels
[GUARD]      Passive Monitoring initialized
[GUARD]      Mode: Post-execution analysis
[GUARD]      Auto-blocking: DISABLED (observation only)
[AFFINITY]   Policy built for 8 cores
[AFFINITY]   Heavy:  [1, 2, 3, 4, 5, 6, 7, 8]  preferred=1
[AFFINITY]   Medium: [2, 3, 4, 5, 6, 7, 8]  preferred=2
[AFFINITY]   Light:  [3, 4, 5, 6, 7, 8]  preferred=3
[COORD]      Overflow guard initialized
[COORD]      Guard House initialized
[COORD]      Core affinity policy created
[COORD]      Building execution pipeline...
[WORKER]     CorePinnedQueue initialized  cores=8  workers_per_core=4  total=32
[COORD]      Worker queue created
[COORD]      Admission gate configured
[COORD]      Configuring convergence engine...
[COORD]      Prometheus convergence enabled
[COORD]      Ready!
[COORD]        Cores:            8
[COORD]        Workers per core: 4
[COORD]        Total workers:    32
[COORD]        Convergence:      ENABLED
[COORD]      ============================================================
[COORD]      Starting...
[WORKER]     Starting 32 mailbox workers...
[WORKER]     Started 32 workers across 8 cores
[WORKER]       [->]  worker_0_core_1 started  core=1  local=0
[WORKER]       [->]  worker_1_core_1 started  core=1  local=1
[WORKER]       [->]  worker_2_core_1 started  core=1  local=2
[WORKER]       [->]  worker_3_core_1 started  core=1  local=3
[WORKER]       [->]  worker_4_core_2 started  core=2  local=0
[WORKER]       [->]  worker_5_core_2 started  core=2  local=1
[WORKER]       [->]  worker_6_core_2 started  core=2  local=2
[WORKER]       [->]  worker_7_core_2 started  core=2  local=3
[WORKER]       [->]  worker_8_core_3 started  core=3  local=0
[WORKER]       [->]  worker_9_core_3 started  core=3  local=1
[WORKER]       [->]  worker_10_core_3 started  core=3  local=2
[WORKER]       [->]  worker_11_core_3 started  core=3  local=3
[WORKER]       [->]  worker_12_core_4 started  core=4  local=0
[WORKER]       [->]  worker_13_core_4 started  core=4  local=1
[WORKER]       [->]  worker_14_core_4 started  core=4  local=2
[WORKER]       [->]  worker_15_core_4 started  core=4  local=3
[WORKER]       [->]  worker_16_core_5 started  core=5  local=0
[WORKER]       [->]  worker_17_core_5 started  core=5  local=1
[WORKER]       [->]  worker_18_core_5 started  core=5  local=2
[WORKER]       [->]  worker_19_core_5 started  core=5  local=3
[WORKER]       [->]  worker_20_core_6 started  core=6  local=0
[WORKER]       [->]  worker_21_core_6 started  core=6  local=1
[WORKER]       [->]  worker_22_core_6 started  core=6  local=2
[WORKER]       [->]  worker_23_core_6 started  core=6  local=3
[WORKER]       [->]  worker_24_core_7 started  core=7  local=0
[WORKER]       [->]  worker_25_core_7 started  core=7  local=1
[WORKER]       [->]  worker_26_core_7 started  core=7  local=2
[WORKER]       [->]  worker_27_core_7 started  core=7  local=3
[WORKER]       [->]  worker_28_core_8 started  core=8  local=0
[WORKER]       [->]  worker_29_core_8 started  core=8  local=1
[WORKER]       [->]  worker_30_core_8 started  core=8  local=2
[WORKER]       [->]  worker_31_core_8 started  core=8  local=3
[GATE]       Admission gate started — full saturation mode
[GATE]       Admission loop started — unrestricted flow
[COORD]      Event loop started
[COORD]      Worker queue started
[COORD]      Admission gate started
[COORD]      Convergence monitoring started
[COORD]      Started successfully!
  ✓ Coordinator started.
```

*Example outputs from the logging system that demonstrate observability.*
</summary>


> Objective: Show observability.

```python
# "prometheus_convergence.py" is a metrics system which operates a  
# worker convergence detector. If the system detects a convergence   
# event (e.g., a surge of task difficulty), it can trigger a hot-swap  
# to different worker counts and mitigate the issue. This maintains   
# control over task flow and improves system stability during high   
# load periods.  

# To provide visibility into the system's behavior,   
# users can choose to log high-level events.

verbose: bool = False # Look for these
convergence_verbose: bool = False
```

**Shutdown & Print Visibility:**
```terminaloutput
[ROUTING] Token list_process_1773226309150654300 (medium) → Pos 11620 (Core 2, Pattern 3)
[ROUTING] Token string_ops_1773226309151156500 (light) → Pos 11655 (Core 6, Pattern 4)
[ROUTING] Token simple_loop_1773226309164460300 (light) → Pos 11658 (Core 7, Pattern 4)
[WORKER_9_CORE_4] ✓ Completed series_short_1773226309150654300
[DEBUG] Recorded: series_short_1773226309150654300
[WORKER_15_CORE_6] ✓ Completed string_ops_1773226309151156500
[DEBUG] Recorded: string_ops_1773226309151156500
[WORKER_18_CORE_7] ✓ Completed simple_loop_1773226309164460300
[DEBUG] Recorded: simple_loop_1773226309164460300
  Stopping coordinator...

[COORD]      Stopping...
[GATE]       Admission gate stopped
[WORKER]     Stopping all workers...
[WORKER]       [->]  worker_0_core_1 stopped
[WORKER]       [->]  worker_1_core_1 stopped
[WORKER]       [->]  worker_2_core_1 stopped
[WORKER]       [->]  worker_3_core_1 stopped
[WORKER]       [->]  worker_4_core_2 stopped
[WORKER]       [->]  worker_5_core_2 stopped
[WORKER]       [->]  worker_6_core_2 stopped
[WORKER]       [->]  worker_7_core_2 stopped
[WORKER]       [->]  worker_8_core_3 stopped
[WORKER]       [->]  worker_9_core_3 stopped
[WORKER]       [->]  worker_10_core_3 stopped
[WORKER]       [->]  worker_11_core_3 stopped
[WORKER]       [->]  worker_12_core_4 stopped
[WORKER]       [->]  worker_13_core_4 stopped
[WORKER]       [->]  worker_14_core_4 stopped
[WORKER]       [->]  worker_15_core_4 stopped
[WORKER]       [->]  worker_16_core_5 stopped
[WORKER]       [->]  worker_17_core_5 stopped
[WORKER]       [->]  worker_18_core_5 stopped
[WORKER]       [->]  worker_19_core_5 stopped
[WORKER]       [->]  worker_20_core_6 stopped
[WORKER]       [->]  worker_21_core_6 stopped
[WORKER]       [->]  worker_22_core_6 stopped
[WORKER]       [->]  worker_23_core_6 stopped
[WORKER]       [->]  worker_24_core_7 stopped
[WORKER]       [->]  worker_25_core_7 stopped
[WORKER]       [->]  worker_26_core_7 stopped
[WORKER]       [->]  worker_27_core_7 stopped
[WORKER]       [->]  worker_28_core_8 stopped
[WORKER]       [->]  worker_29_core_8 stopped
[WORKER]       [->]  worker_30_core_8 stopped
[WORKER]       [->]  worker_31_core_8 stopped
[WORKER]     All workers stopped
[COORD]      All components stopped
[COORD]      Shutdown complete
  ✓ Coordinator stopped.
```
</details>

[↑ Top](#scope)

---
# Conclusion

---

What was shown — Ordinary Python functions can be scheduled as   
token-managed operations, and CPU-centric and I/O-centric workloads   
can be coordinated under the same execution model.

---

What was excluded — All GUI, launcher/web flow, and Guard House  
patterns were excluded from this proof to focus on the core TokenGate  
architecture.

---  

For the complete architecture including WebSocket  
orchestration and recovery mechanisms, see [proof-of-concept.md](proof-of-concept.md).  
  

## Limits of Claims  

This proof of concept and active beta does not establish a new concurrency  
primitive in Python, nor does it claim replacement of native threading or asyncio   
semantics. It demonstrates a task model in which tokens are used to bind   
metadata, admission state, routing policy, and completion handling across   
an asyncio-driven coordinator and thread-backed execution workers. 