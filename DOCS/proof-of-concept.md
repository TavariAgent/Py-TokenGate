# Proof of Concept: TokenGate

**This documents use of async coordination and thread backed execution working  
using task tokens.**  

***This is a proof of concept not a finished product.***

---

### Overview of Documented Proofs: 

1. Productivity and methodology with results
2. [Task Acceptance Criteria](#2-task-acceptance-criteria)
3. [Event Bus Correctness and Concurrency](#3-event-bus-correctness-and-concurrency)
4. [WebSocket Interface](#4-orchestration-of-tasks-through-websocket)
5. [Product Safety](#5-product-recovery-mechanisms)

- [Conclusion](#conclusion)

---

## 1. Productivity

### Overview:

Token tasks are the paradigm that connects async and threading.

- Simulated workloads can be effectively managed using tokens 
- TokenGate handles standalone, chained, nested, and mixed workloads  
- Performance is measured based on parallelism and latency under various loads

---

### Objective:

To show the productivity of TokenGate by simulating various workloads.

---

### Task Simulation:

***All tasks were submitted on an 8 core Ryzen 7800X3D***

> #### Tasks were simulated to reflect real-world workloads, call chains, and concurrency.

```terminaloutput
CONCURRENCY BURST: Medium x8 | release 1954 (8 tasks)
======================================================================
  Submit spread (barrier jitter): 0.20ms
  Overall wall-clock:             0.009419s
  Min task duration:              0.008262s
  Max task duration:              0.008825s
  Mean task duration:             0.008539s
  Stdev (clustering indicator):   0.000196s

  Duration per task (tight clustering = true concurrency):
    Task 00: 0.008419s  
    Task 01: 0.008339s  
    Task 02: 0.008575s  
    Task 03: 0.008641s  
    Task 04: 0.008501s  
    Task 05: 0.008749s  
    Task 06: 0.008825s  
    Task 07: 0.008262s  

  Serial estimate (sum):  0.068311s
  Actual wall-clock:      0.009419s
  Concurrency ratio:      7.25x  (concurrent)

CONCURRENCY BURST [Medium x8 | release 1954] PASSED
======================================================================
CONCURRENCY WINDOW: Sustained mixed releases (40s)
======================================================================
  Releases:                       2016
  Total tasks:                    16128
  Overall wall-clock:             40.001710s
  Min task duration:              0.001093s
  Max task duration:              0.123642s
  Mean task duration:             0.014570s
  Stdev (clustering indicator):   0.025743s

  Serial estimate (sum):          234.981666s
  Actual wall-clock:              40.001710s
  Sustained concurrency ratio:    5.87x  (concurrent)

CONCURRENCY WINDOW [Sustained mixed releases (40s)] PASSED
======================================================================
PARALLEL CHAINS: Concurrent entry, ordered resolution per chain
======================================================================
Running 4 chains with concurrent step-0 submission.

  Chain 1: {'parallel_chain_1': 2130, 'parity': 'even'}
  Chain 2: {'parallel_chain_2': 3310, 'parity': 'even'}
  Chain 3: {'parallel_chain_3': 9920, 'parity': 'even'}

  4 parallel chains completed in 0.000s
PARALLEL CHAINS PASSED — chains concurrent, steps ordered within each.
```
> The system has varied performance but rarely falls under 4x concurrency in  
> sustained bursts, and can achieve 7.25x concurrency in ideal conditions.
> 

### Usage:

> #### Example of token-threaded tasks in a saturated scenario.

```python
import time
import math
from operations_coordinator import OperationsCoordinator
from token_system import task_token_guard

@task_token_guard(operation_type='series_long', tags={'weight': 'heavy'})
def series_long(duration: float = 2.0):
    """
    Occupies a worker for ~duration seconds.
    Heavy weight — intended to occupy core 1 workers and create visible backpressure
    when submitted in batches. Use this to demonstrate that the mailbox bounds
    submissions rather than allowing runaway queue growth.
    """
    end = time.monotonic() + duration
    acc = 2.0
    step = 0
    while time.monotonic() < end:
        acc = math.sqrt(abs(math.cos(acc))) + math.log(step + 2)
        step += 1
    return round(acc, 6)

def run_series_saturation_demo():
    """
    Submits a burst of series_long tasks that intentionally exceed the
    typical worker count. The goal is to make backpressure visible:
    early tokens resolve quickly, later tokens wait in the mailbox.

    Observe: submission is fast (near-instant), but resolution times will show a clear spread.
    The first batch of tasks occupies the workers and subsequent tasks queue up in the mailbox.
    """
    coordinator = OperationsCoordinator()
    coordinator.start()
    BURST_SIZE = 20
    DURATION_PER_TASK = 1.0  # seconds each worker is held


    print(f"\n{'=' * 70}")
    print("SERIES SATURATION: Intentional pool pressure")
    print(f"{'=' * 70}")
    print(f"Submitting {BURST_SIZE} x {DURATION_PER_TASK}s tasks.")
    print("Workers will be occupied. Later tokens will wait in mailbox.\n")

    tokens = []
    submit_times = []
    
    try:
        for i in range(BURST_SIZE):
            t = time.monotonic()
            token = series_long(DURATION_PER_TASK)
            tokens.append(token)
            submit_times.append(t)
    
        results = []
        for i, token in enumerate(tokens):
            result = token.get(timeout=60)
            elapsed = time.monotonic() - submit_times[i]
            results.append((i, elapsed, result))
    
        print(f"\n  Earliest resolution: {min(r[1] for r in results):.3f}s")
        print(f"  Latest  resolution: {max(r[1] for r in results):.3f}s")
        print(f"  Spread (backpressure window): "
              f"{max(r[1] for r in results) - min(r[1] for r in results):.3f}s")
        print("\nSERIES SATURATION PASSED — backpressure observed, no failures.")
        return results
    
    finally:
        coordinator.stop()

if __name__=="__main__":
    run_series_saturation_demo()
```  

### Performance Metrics Results:

 > #### The effectiveness of TokenGate in terms of throughput and latency under various loads.

```terminaloutput
MIXED ORCHESTRATOR COMPLETE (3482 tasks, 40s sustained burst at then end and multiple waves.)
======================================================================
  Total wall-clock : 120.8s
  Waves            : 232
  Submitted        : 3482
  Resolved         : 3482  (100.0%)
  Failed           : 0
  Latency p50      : 0.014s
  Latency p95      : 0.039s
  Latency max      : 0.063s

  Operations dispatched:
    string_operation                      361  ████████████████████████████████████████████████████████████████████████
    fibonacci_operation                   332  ██████████████████████████████████████████████████████████████████
    moderate_operation                    324  ████████████████████████████████████████████████████████████████
    complex_operation                     322  ████████████████████████████████████████████████████████████████
    prime_operation                       317  ███████████████████████████████████████████████████████████████
    simple_operation                      315  ███████████████████████████████████████████████████████████████
    write_blob_moderate                   315  ███████████████████████████████████████████████████████████████
    write_json_fast                       306  █████████████████████████████████████████████████████████████
    append_log_slow                       305  █████████████████████████████████████████████████████████████
    trivial_operation                     301  ████████████████████████████████████████████████████████████
    heavy_operation                       284  ████████████████████████████████████████████████████████
  
```

[↑ Top](#proof-of-concept-tokengate)

---

## 2. Task Acceptance Criteria

### Overview:

**Task acceptance criteria** are the rules and conditions that determine whether a task can be accepted for   
processing using TokenGate.

- "Accepted" tasks are those that meet specific criteria for processing.  
- Threading work can be accepted based on characteristics such as "CPU-centric" or "I/O-centric".
- Decorated functions are wrapped internally to produce token snapshots for mixed workloads.  
- At minimum, three lines are required to produce a token snapshot for a decorated function.
- `async def function()` cannot be decorated, `def function()` is required for task submission.

---

### Objective:

To establish the criteria for task acceptance in a token-based concurrency management system.

### Setup:

1. **Task Types:** Categorizes tasks based on their characteristics (e.g. CPU, I/O).
2. **Token Assignment:** Prints a token assignment strategy that prioritizes tasks based on their requirements.
3. **Acceptance Criteria:** Examples of decoratable functions the system can accept.

---

### Types:

> #### The system categorizes tasks based on their characteristics.

```python
from enum import Enum

class TaskWeight(Enum):
    """Task weight classification for CPU routing."""
    HEAVY = "heavy"  # High difficulty work gets Core 1
    MEDIUM = "medium"  # Balanced work gets Core 2+
    LIGHT = "light"  # Simple work gets Core 3+

STORAGE_SPEEDS = {
    'SLOW': 10,  # HDD, network drives, slow storage
    'MODERATE': 25,  # SATA SSD, decent performance
    'FAST': 50,  # NVMe like MP700, high performance
    'INSANE': 70  # Optane, RAM disk, extreme performance
}

# CPU related string tags are not considered "systems critical" whereas storage_speed based strings 
# are emphasized as critical to prevent tag assignment errors. Storage support is a crucial factor in   
# determining the number of concurrent writers to avoid throttling and ensure tasks execute.
```

> Consideration for storage speed is crucial to prevent task failure.

### Assignment:

> #### Token assignments are based on task characteristics, all system policies visible on startup.

```terminaloutput
[COORD] Detecting CPU topology...
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

### Acceptance:

> #### Criteria for accepting tasks into the system using safe practices.

```python
import time
import random
import json
import asyncio
from operations_coordinator import OperationsCoordinator
from token_system import task_token_guard


@task_token_guard(
    operation_type='json_file_moderate',  
    # "Operation type" is for internal tracking and can be anything.
    tags={
        'weight': 'medium',
        # "weight" is denoted by "light", "medium", or "heavy" and guides   
        # worker assignment based on the expected performance of the task.
        'storage_speed': 'MODERATE'
        # "storage_speed" is for establishing an efficient amount of concurrent writers.
    }
)   # Indicators for storage speed are "SLOW" (10 concurrent writes), "MODERATE" (25 concurrent), 
    # "FAST" (50 concurrent), and "INSANE" (70 concurrent). 
def create_json_file(index: int) -> dict:
    """Simulates a task that creates a JSON file with moderate storage speed."""
    filename = f"output_{index}.json"
    data = {
        "index": index,
        "random_value": random.randint(0, 100_000),
        "timestamp": time.time(),
        "message": f"This is a simulated JSON file for task {index}.",
    }

    time.sleep(0.5)

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return data

# Example of a supported main function that starts the event bus for tasks to link the coordinator. 
# The coordinator manages the execution of these tasks based on concurrency management policies.
# DO NOT USE decorated "async def my_functions(...):", the decorator provides the async linkage  
# needed for coordinator-managed execution.
async def main():
    coordinator = OperationsCoordinator()
    coordinator.start()
    try:
        # Simulate submitting multiple tasks
        for i in range(10):
            create_json_file(i)
    finally:
        coordinator.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

> #### Tasks such as string manipulation, IO writes, and mathematical operations can be accepted.

```python
import os
import time
import math
from operations_coordinator import OperationsCoordinator
from token_system import task_token_guard

@task_token_guard(operation_type='list_process', tags={'weight': 'medium'})
def moderate_operation(size):
    """Medium: List comprehension and filtering."""
    data = [i * 2 for i in range(size)]
    filtered = [x for x in data if x % 3 == 0]
    return sum(filtered)

@task_token_guard(operation_type='string_ops', tags={'weight': 'light'})
def string_operation(length):
    """String manipulation operations."""
    s = "test" * length
    return len(s.upper().replace("T", "X").split("X"))

@task_token_guard(
    operation_type='write_blob_moderate',
    tags={'weight': 'medium', 'storage_speed': 'fast'}
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

[↑ Top](#proof-of-concept-tokengate)

---

## 3. Event Bus Correctness And Concurrency



### Overview:

#### Establishing the event bus

- TokenGate utilizes semaphores and locks to manage token safety.  
- The event bus depends on asyncio and threading for task execution.   
- Tokens have a lifecycle, creation, assignment, execution, and completion.  
- Mailbox queues are used to manage communication between threads and async tasks.
- Mailboxes house tokens to be processed in a first-in-first-out (FIFO) formation.

----

### Objective:

To show that the TokenGate event bus can maintain correctness and handle concurrency.

### Setup:

1. **Event Bus Implementation:** How the event bus uses semaphores and locks
2. **Token Lifecycle Management:** Token lifecycle from creation, assignment, execution, and completion.
3. **Mailbox Queues:** Queues that manage communication between threads and async tasks.
4. **Pre-emptive Scheduling:** Ensures that tokens respect staggered positions and allowed workers.

---

### Event Bus:

> #### The event bus utilizes semaphores and locks.

```python
def check_method_allowed(self, func: Callable, operation_type: str):
    """
    Check if a method is allowed to execute (not blocked).

    Called before execution to enforce auto-blocking.

    Args:
        func: The function being called
        operation_type: Operation type

    Raises:
        MethodBlockedException: If the method is auto-blocked
    """
    if not self.auto_block_dangerous:
        return  # Auto-blocking disabled

    method_name = func.__name__

    with self._lock:
        if method_name in self.blocked_methods:
            rep = self.method_reputations.get(method_name)
            if rep:
                failure_rate = rep.get_failure_rate()
                raise MethodBlockedException(
                    f"Method '{method_name}' is auto-blocked due to high failure rate "
                    f"({failure_rate:.1f}%, {rep.failed_executions}/{rep.total_attempts} attempts). "
                    f"Review method implementation before re-enabling."
                )
            else:
                # Graceful handling for methods without reputation data
                raise MethodBlockedException(
                    f"Method '{method_name}' is auto-blocked."
                )

def throttle(self, func: Callable, *args, **kwargs) -> Any:
    """
    Execute a function with I/O throttling.

    Args:
        func: Function to execute (should perform I/O)
        *args: Function arguments
        **kwargs: Function keyword arguments

    Returns:
        Function result
    """
    # Record attempt
    with self._lock:
        self.total_operations += 1

    # Wait for I/O slot
    wait_start = time.time()
    acquired = self._semaphore.acquire(blocking=True)
    wait_duration = time.time() - wait_start

    if not acquired:
        raise Exception(f"Failed to acquire I/O slot for {self.speed_tier}")

    try:
        # Track wait time and active count
        with self._lock:
            self.total_wait_time += wait_duration
            self.current_active += 1

        # Execute the I/O operation
        result = func(*args, **kwargs)

        return result

    finally:
        # Release I/O slot
        with self._lock:
            self.current_active -= 1
        self._semaphore.release()
```

> In this architecture, asyncio provides the admission and routing foundation,  
> while thread-backed workers perform task execution

### Management:

> #### The lifecycle of tokens from creation, assignment, and execution.


```python
# Decorator, wrapper for token snapshot and creation
def task_token_guard(
    operation_type: Optional[str] = None,
    tags: Optional[Dict[str, Any]] = None,
) -> Callable[[Callable[P, R]], Callable[P, "TaskToken[R]"]]:
    """Decorate a callable so calls return TaskToken instead of executing immediately.

    The wrapper performs optional code analysis, optional quarantine checks,
    optional storage-speed throttling, and token creation through the global
    token pool.

    Args:
        operation_type: Stable operation label used for metadata and routing.
        tags: Optional routing and policy tags, such as weight or storage tier.

    Returns:
        A decorator that replaces direct execution with token submission.

    Notes:
        The wrapped callable is not executed at call time. It is captured as a
        token-managed task for later admission and execution.
    """
    def decorator(func: Callable[P, R]) -> Callable[P, "TaskToken[R]"]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> "TaskToken[R]":
            from .code_inspector import CodeInspector
            from .spike_detector import TokenQuarantinedException

            if not hasattr(wrapper, "cached_metrics"):
                wrapper.cached_metrics = CodeInspector.analyze(func)
            metrics = wrapper.cached_metrics

            final_tags = dict(tags) if tags else {} # Immutable copy of tags
            final_func = func
        # ... Additional logic for handling tokens ...

# The admission loop is an async function that continuously routes tokens.
async def _admission_loop(self):
    """
    Pure pass-through routing loop.

    No throttling - tokens flow directly from the pool to the execution queue.
    """
    print("[GATE] Admission loop started - unrestricted flow")

    while self._active:
        try:
            # Get next token from pool
            token = await self.token_pool.get_next_token()

            # Skip killed tokens
            if token.is_killed():
                continue

            # Route directly to execution
            await self._admit_token(token)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[GATE] Error in admission loop: {e}")
            await asyncio.sleep(0.1)  # Brief pause on error only

# Admits tokens for execution.
async def _admit_token(self, token: TaskToken):
    """
    Route token to worker queue.

    Simple state transition + queue insertion.
    """
    # Transition state
    if not token.transition_state(TokenState.ADMITTED):
        print(f"[GATE] Failed to admit token {token.token_id} (state: {token.state})")
        return

    if token.state == TokenState.CREATED:
        token.transition_state(TokenState.WAITING)

    # Route to worker queue
    await self.worker_queue.put(token)

    # Update metrics
    self.total_admitted += 1
    self.token_pool.total_admitted += 1

# The core affinity policy determines which cores   
# are preferred for tasks based on their weight.
def get_valid_cores_for_weight(self, weight: TaskWeight) -> List[int]:
    """Get list of cores that can handle this weight."""
    return self.policy.get_preference_chain(weight)

# All system routing decisions funnel through this function,   
def record_task_routed(self, core_id: int, weight: TaskWeight):
    """Called by CorePinnedStaggeredQueue when it routes a task."""
    with self._routing_lock:
        self.total_routed += 1
        self._affinity_counts[core_id][weight.value] += 1    
    
# Async gathers task tokens setting them to the correct state for execution with workers.
async def _execute_token(self, token: TaskToken, worker_id: str, core_id: int):
    """Execute one admitted token on its already-selected core path.

    This method performs the lifecycle transition to EXECUTING, runs the
    wrapped callable through the executor-backed path, stores the result or
    error on the token, records execution history for the coordinator, and
    triggers retry/Guard House hooks when configured.
    """
    # Transition to executing
    if not token.transition_state(TokenState.EXECUTING):
        tg_print('worker', f'{worker_id} failed to transition {token.token_id}', level='warn')
        return

    start_time = time.time()
    success = False

    try:
        loop = asyncio.get_running_loop()
        # Token execution is offloaded to a thread pool executer and looped through the  
        # data `_execute_token_wrapped` for data locality. The token's wrapped callable  
        # is executed in a thread to avoid blocking the event loop.
        _execute_token_wrapped = partial(self._execute_token_wrapped, token) 
        result = await loop.run_in_executor(None, _execute_token_wrapped)
        token.set_result(result)
        self.total_executed += 1
        success = True

        tg_print('worker', f'{worker_id} completed {token.token_id}', level='state')

    finally:
        execution_duration = time.time() - start_time
    # Additional execution recording and Guard House checks would go below here.
```

### Mailboxes:

> #### Mailbox queues manage communication between threads and async tasks.


```python
# Workers are indexed to route correctly and track task flow through the system.
def _worker_index(self, core_id: int, local_i: int) -> int:
    return (core_id - 1) * self.workers_per_core + local_i

# When a token is ready for execution, the system chooses the least  
# loaded local worker for the assigned core with consideration for affinity.
def _choose_local_worker_least_loaded(self, core_id: int) -> int:
    active = int(self.core_patterns.get(core_id, self.workers_per_core))
    active = max(1, min(self.workers_per_core, active))

    best_local = 0
    best_len = 1 << 60

    for local_i in range(active):
        q = self.mailboxes[(core_id, local_i)]
        qlen = q.qsize()
        if qlen < best_len:
            best_len = qlen
            best_local = local_i

    return best_local
```

>Tokens are processed in a first-in-first-out (FIFO) formation within the mailboxes, ensuring orderly execution.

### Scheduling:

> #### Tokens respect staggered positions and allowed workers with affinity for cores.

```python
# The system increments and uses preference chains to guide   
# tokens to their respective mailbox positions based on weight.
def _build_preferences(self):
    """
    Build preference chains for each weight.

    Rules:
    - Heavy: cores [1, 2, 3, ...] (starts at 1)
    - Medium: cores [2, 3, 4, ...] (starts at 2, NEVER core 1)
    - Light: cores [3, 4, 5, ...] (starts at 3, NEVER cores 1-2)

    On systems with few cores (2 cores):
    - Heavy: [1, 2]
    - Medium: [2]
    - Light: [2]
    """
    # Heavy can use ALL cores, prefers Core 1
    heavy_cores = list(range(1, self.num_cores + 1))

    # Medium starts at Core 2 (NEVER Core 1)
    medium_cores = list(range(2, self.num_cores + 1)) if self.num_cores >= 2 else []
    if not medium_cores:
        # Fallback for 1-core system
        medium_cores = [1]

    # Light starts at Core 3 (NEVER Cores 1-2)
    light_cores = list(range(3, self.num_cores + 1)) if self.num_cores >= 3 else []
    if not light_cores:
        # Fallback: use medium's cores
        light_cores = medium_cores

    self.preferences = {
        TaskWeight.HEAVY: CorePreference(
            allowed_cores=heavy_cores,
            preferred_core=heavy_cores[0]
        ),
        TaskWeight.MEDIUM: CorePreference(
            allowed_cores=medium_cores,
            preferred_core=medium_cores[0]
        ),
        TaskWeight.LIGHT: CorePreference(
            allowed_cores=light_cores,
            preferred_core=light_cores[0]
        )
    }

def get_preference_chain(self, weight: TaskWeight) -> List[int]:
    """Get an ordered list of cores to try for this weight."""
    return self.preferences[weight].allowed_cores

# The system uses "chain position routing" to ensure that tokens are processed in the order  
# they were received, while still respecting their core affinity and mailbox placements.
# The exact order is: Admission -> core preference routing -> mailbox assignment -> worker execution.
def can_use_core(self, weight: TaskWeight, core_id: int) -> bool:
    """Check if this weight is allowed on this core."""
    return core_id in self.preferences[weight].allowed_cores
```

> Core preference chains and mailbox routing constrain where tokens may execute and how   
> they are distributed under load. The routing mechanisms are core identity routing, chain  
> position routing, and load-based.

[↑ Top](#proof-of-concept-tokengate)

---
## 4. Orchestration of Tasks Through WebSocket

### Overview:

#### Orchestrating Loads through WebSocket

* WebSockets manage real-time communication between the dashboard client and TokenGate.
* The interface provides live visibility into token pool state, worker activity, and Guard House status.
* Password gating restricts access so only authorized users can issue control commands.
* Tasks can be monitored, launched, and controlled through a centralized dashboard without touching the codebase.

---

### Objective:

To demonstrate that TokenGate loads can be observed and controlled in real time through
a WebSocket interface, and that administrative operations such as pool drain produce
honest, consistent results across all monitoring layers.

### Setup:

1. **WebSocket Implementation:** Flask-SocketIO server with password-gated access.
2. **Interface Controls:** Dashboard panels for token pool state, core affinity, Guard House status, and recent executions.
3. **Access Control:** Password authentication gates all admin operations.
4. **Task Monitoring:** Live execution feed with per-token timing, core assignment, and success state.

---

### WebSocket:

> #### The dashboard connects via WebSocket and reflects live system state.

```
[GUI] Client connected: CKoqNEt8Y-HURE5MAAAB
```

The dashboard exposes four live panels:

- **System Status** — worker count, physical cores, active pattern, uptime
- **Token Pool** — total created, waiting, executing, completed, failed
- **Core Affinity Distribution** — per-core routing breakdown
- **Guard House Status** — methods tracked, execution count, health classification

All panels update in real time via WebSocket push without polling.

---

### Interface:

> #### Token Pool and Guard House report independently.


![TokenGate Dashboard — live run](/assets/dash_working.png)


---

### Control:

> #### Administrative commands operate cleanly without corrupting in-flight state.  

![TokenGate Admin Controls](/assets/per_operation_controls.png)

Two control operations were tested during the WebSocket session:

**Drain Pool** — halts admission, allows all in-flight tokens to complete naturally.
```
# Observed behavior:
  Before drain  →  tokens submitting, Executing > 0
  Drain issued  →  Waiting drops to 0, Executing winds down
  After drain   →  Waiting: 0  Executing: 0  Failed: 0
```

Drain is a safe shutdown mechanism. It does not kill running tokens,
does not corrupt pool state, and does not produce false failure counts.
The system reaches stable idle and can resume without a full restart.

**Kill Specific Token** — marks a specific queued token so it is skipped at dequeue time.

Kill only affects tokens that have not yet been picked up by a worker.
Any token already in `EXECUTING` state is unaffected. The worst outcome
is a `.get(timeout=...)` waiting on the killed token eventually timing out.
Nothing in-flight is touched.

---

### Monitoring:

> #### Guard House passive monitoring confirmed 100% resolution across all tracked methods.
```
EXCELLENT PERFORMERS (>95% success):
─────────────────────────────────────
  cpu_light      Rate: 100.0%  (3248/3248)   Avg: 0.02s
  throttled_func Rate: 100.0%  (1736/1736)   Avg: 0.01s
  cpu_heavy      Rate: 100.0%  (1948/1948)   Avg: 0.01s
  cpu_medium     Rate: 100.0%  (3128/3128)   Avg: 0.02s
```

The execution history dump confirms this at the individual token level:
250 sampled records, all `"success": true`, sub-10ms execution across all
operation types, with affinity routing verified per-record:
```json
{
  "token_id": "cpu_heavy_1773951201569585800",
  "operation_type": "cpu_heavy",
  "success": true,
  "execution_time": 0.006198,
  "core_id": 1,
  "worker_id": "worker_0_core_1",
  "complexity_score": 44.46
}
```

`cpu_heavy` routed exclusively to `core_id: 1` as designed.
`cpu_light` distributed across cores 3–8, never touching core 1.
Complexity scores consistent per operation type across all 250 records —
`code_inspector.py` caching confirmed working correctly.

A full execution history dump is available in `dumps/` for independent verification.  

To learn how to use online control features, see the WebSocket Integration Guide:

[WebSocket Integration Guide](WEBSOCKET.md)

[↑ Top](#proof-of-concept-tokengate)

---

## 5. Product Recovery Mechanisms

### Overview:
Product recovery mechanisms are the strategies and processes implemented to allow the  
system to recover from failures.

- TokenGate detects and recovers from various types of failures observably.
- Recovery mechanisms include retry logic, and queues are bounded to prevent overload.
- The system maintains data integrity and consistency during recovery processes.

---

### Objective:
Formalize TokenGate recovery mechanisms implemented, and how the system detects  
failures, recovers from them, and maintains data integrity.

### Setup:
1. **Failure Detection:** Displays failure detection mechanisms..
2. **Recovery Mechanisms:** Explains the recovery mechanisms.

### Failure:

> #### The system detects various types of failures observably.

```python
async def _worker_loop(self, worker_idx: int, worker_id: str, core_id: int, local_i: int):
    q = self.mailboxes[(core_id, local_i)]
    print(f"[{worker_id}] Started on Core {core_id} (local {local_i})")

    while self._active:
        try:
            token = await q.get()  # blocks efficiently until a token arrives

            # Update depth gauge (dequeue)
            self.core_queue_depth[core_id] = max(0, self.core_queue_depth[core_id] - 1)
            self.metrics.update_queue_depth(core_id, self.core_queue_depth[core_id])

            # Queue wait
            enq = token.metadata.tags.get("enqueued_at")
            if enq is not None:
                self.metrics.record_queue_wait(core_id, time.perf_counter() - float(enq))

            if token.is_killed():
                continue

            await self._execute_token_with_metrics(token, worker_id, core_id)

        except asyncio.CancelledError:
            break
            # Cancellation is expected during shutdown,  
            # so we break the loop without logging an error.
        except Exception as e:
            # Log worker errors but keep the loop running to  
            # allow for recovery and continued processing.
            print(f"[{worker_id}] Error: {e}")
            await asyncio.sleep(0.05)

    print(f"[{worker_id}] Stopped")
        
async def _convergence_loop(self):
    """Background convergence monitoring loop."""
    while self._active:
        try:
            await asyncio.sleep(5.0)

            # Analyze cores
            core_pressures = self.convergence.analyze_cores(self.worker_pool)

            # Get adjustments
            adjustments = self.convergence.recommend_adjustments(core_pressures)

            if adjustments:
                if self.convergence.verbose:
                    # Log adjustments being applied
                    print(f"[CONVERGENCE] Applying {len(adjustments)} pattern adjustments...")
                for core_id, new_pattern in adjustments.items():
                    self.convergence.apply_pattern(core_id, new_pattern, self.worker_pool)

        except Exception as e:
            # Log convergence errors but keep the loop running
            print(f"[CONVERGENCE] Error: {e}")
            await asyncio.sleep(5.0)
            
# Tokens that are identified as problematic can be quarantined for later review and analysis,   
# preserving all relevant information about the token and its execution context.
def quarantine_token(
        self,
        token_id: str,
        method_name: str,
        operation_type: str,
        predicted_complexity: float,
        historical_avg_complexity: float,
        deviation_percent: float,
        args: tuple[Any, ...],
        kwargs: dict,
        reason: str
) -> QuarantinedToken:
    """
    Quarantine a token.

    Preserves all information for later review/replay.

    Args:
        token_id: Token identifier
        method_name: Method name
        operation_type: Operation type
        predicted_complexity: Predicted complexity score
        historical_avg_complexity: Historical average
        deviation_percent: Deviation percentage
        args: Function args (preserved)
        kwargs: Function kwargs (preserved)
        reason: Reason for quarantine

    Returns:
        QuarantinedToken instance
    """
    with self._lock:
        # Serialize args (handle large data carefully)
        args_summary = self._summarize_args(args)
        args_blob = self._serialize_args(args)

        kwargs_summary = self._summarize_kwargs(kwargs)
        kwargs_blob = self._serialize_kwargs(kwargs)

        # Create quarantined token
        quarantined = QuarantinedToken(
            token_id=token_id,
            method_name=method_name,
            operation_type=operation_type,
            predicted_complexity=predicted_complexity,
            historical_avg_complexity=historical_avg_complexity,
            deviation_percent=deviation_percent * 100,  # Store as percentage
            args_summary=args_summary,
            args_blob=args_blob,
            kwargs_summary=kwargs_summary,
            kwargs_blob=kwargs_blob,
            timestamp=time.time(),
            quarantine_reason=reason
        )

        self.quarantined_tokens.append(quarantined)
        self.total_quarantined += 1

        # Save to disk
        self._save_quarantine()

        print(f"[QUARANTINE] Token quarantined: {token_id}")
        print(f"  Method: {method_name}")
        print(f"  Deviation: {deviation_percent * 100:.1f}%")
        print(f"  Reason: {reason}")

        return quarantined
```

### Recovery:

> #### Recovery mechanisms include retry logic, and queues are bounded to prevent overload.


```python
# Capped mailbox length to prevent runaway memory
self.MAILBOX_MAX = 120 # Max tokens per worker mailbox

def _inject_retry_to_pool(self, retry_token: TaskToken):
    """
    Inject retry token directly into the global token pool.

    This BYPASSES the admission gate - retries are priority!
    """
    # Add to pool's token dict
    with global_token_pool._lock:
        global_token_pool.tokens[retry_token.token_id] = retry_token

    # Queue for admission processing (it will be admitted immediately)
    if global_token_pool._event_loop:
        import asyncio
        asyncio.run_coroutine_threadsafe(
            global_token_pool._token_queue.put(retry_token),
            global_token_pool._event_loop
        )

    # Transition to waiting state
    retry_token.transition_state(TokenState.WAITING)

    print(f"[GUARD] Injected retry token {retry_token.token_id} directly to pool")
```

[↑ Top](#proof-of-concept-tokengate)

---

# Conclusion

TokenGate is progressing well, beta is seeing a couple hundred active users, if people are enjoying  
the system dropping a star helps immensely.

---

#### What wasn't shown?  
Various internal mechanisms and optimizations were not shown in this proof of concept.

#### What was Proven?

The system displayed the ability to manage task execution with core affinity, handle   
concurrency through worker mailboxes, and provide real-time monitoring and control via   
WebSocket.

#### What's Next?

- Further WebSocket integrations. (e.g. adding more information and pages)
- Tighten integration between components.
- Explore more advanced recovery mechanisms and failure modes.
- Continue optimizing for performance and scalability.

*TokenGate: the limit isn’t speed, it’s parallelism.*