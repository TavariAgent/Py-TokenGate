# TokenGate Architecture Deep Dive

This document provides a deeper look into the architecture and design principles of TokenGate. It is intended for   
developers who want to understand the inner workings of the system, contribute to its development, or build   
advanced integrations.   

Quick navigation:
- [1.5. Pythonic Fidelity & The Pure Python Advantage](#15-pythonic-fidelity--the-pure-python-advantage)
- [2. The Dual-Plane Architecture](#2-the-dual-plane-architecture)
- [3. Intelligent Inspection & Byte-Level Optimization](#3-intelligent-inspection--byte-level-optimization)
- [4. The Convergence Engine: Dynamic Scaling & Feedback Loops](#4-the-convergence-engine--worker-affinity)
- [5. Token Life Cycle & Queue Mechanics](#5-token-lifecycle--queue-mechanics)
- [6. Surgical Control & Active DoS Defense](#6-surgical-control--active-dos-defense)
- [7. Future Horizons: Distributed Compute](#7-future-horizons-distributed-compute)

---

## 1. The Philosophy: Discipline

#### *The threading model itself must remain disciplined or it'll encroach the users codebase*

Threading models are essential for building responsive, layered applications. However, the inherent complexity of   
raw threading often leads to race conditions and brittle codebases when not designed with architectural discipline.   
Instead of forcing developers to manually micromanage threads, locks, and semaphores, TokenGate introduces a   
more elegant solution: **tokenize the request, and route it to threaded workers via an async event loop for   
minimum boilerplate.**

### The Premise:

#### *"Python's ultimate bottleneck is parallelism" is a misconception.*  

While Python's Global Interpreter Lock (GIL) limits generic multi-threading, this limitation is not absolute. By   
enforcing a strict separation of concerns where tasks are tokenized in an async control plane and executed in a   
dedicated threading plane we can leverage the native strengths of both paradigms without fighting either. This   
model enables high-volume concurrency without blocking the main thread or exhausting system resources.

### The Goal:

#### *Achieve true parallelism without the extreme cognitive tax.*  

TokenGate began with a simple question: *"Can I simplify `threading` by using `asyncio` to manage tasks via   
tokens?"* The result is a surprisingly robust concurrency engine that eliminates the mental overhead of   
traditional state management. 

By structuring the system around tokens, it abstracts away complex workflow coordination. Developers can focus   
entirely on the business logic of their CPU-bound or I/O-bound tasks, while the token engine handles the intricacies   
of admission, routing, and lifecycle state. This approach not only dramatically simplifies development but unlocks   
new possibilities for high-throughput Python backends.

### The Strategy:

#### *Fuse `asyncio` into `threading` using tokens as the universal middleman.*  

The primary "pressure points" in any hybrid concurrency model are task submission, execution routing, and state   
aggregation. To build a reliable engine, you must continuously answer: *"How do I approve this task for execution?   
How do I ensure it is processed efficiently? How do I group and label the results?"*   

To manage these branching complexities without bloating the codebase, TokenGate employs **unified control   
surfaces**. Rather than building sprawling, single-use functions for every operation, the architecture utilizes intelligent   
layering and metadata. A single, flexible interface delegates commands down to deeply encapsulated subsystems,   
recycling logic abstractly to reduce mental load.

**Example: Unified Control Surfaces & Subsystem Delegation** 

Notice below how the `OperationsCoordinator` acts as a clean, unified surface. It gathers stats from isolated   
subsystems and reuses the same fundamental commands (`pause`, `drain`, `resume`) to act on both global   
pools and granular token types.

```python
def get_stats(self) -> Dict:
    """Return a composite snapshot of coordinator and subsystem statistics."""
    return {
        'active': self._active,
        'topology': {
            'physical_cores': self.topology.physical_cores,
            'logical_cores': self.topology.logical_cores,
            'workers_per_core': self.workers_per_core,
            'total_workers': self.topology.physical_cores * self.workers_per_core
        },
        # Subsystems maintain their own state, the coordinator just surfaces it
        'token_pool': global_token_pool.get_stats(),
        'admission_gate': self.gate.get_stats(),
        'worker_queue': self.worker_queue.get_stats(),
        'affinity_distribution': self.affinity_queue.get_affinity_report(),
        'convergence': self.convergence.get_convergence_status() if self.convergence else None
    }

# --- Reusing flexible logical surfaces for Token Admin Controls ---

def kill_token(self, token_id: str, reason: str = "admin_override") -> bool:
    return global_token_pool.kill_token(token_id, reason)

def kill_all_by_operation(self, operation_type: str, reason: str = "admin_bulk_kill") -> int:
    return global_token_pool.kill_all_by_operation(operation_type, reason)

# Global State Controls
def pause_admission(self):
    global_token_pool.pause() 

def resume_admission(self):
    global_token_pool.resume()

def drain_pool(self) -> int:
    return global_token_pool.drain()

# Granular/Per-Operation State Controls (Recycling the same underlying methods)
def pause_operation(self, operation_type: str, reason: str = "admin_per-token_pause") -> int:
    return global_token_pool.pause(operation_type, reason) 

def resume_operation(self, operation_type: str, reason: str = "admin_per-token_resume") -> int:
    return global_token_pool.resume(operation_type, reason) 

def drain_operation(self, operation_type: str, reason: str = "admin_per-token_drain") -> int:
    return global_token_pool.drain(operation_type, reason)
```

[Jump to top](#tokengate-architecture-deep-dive)

---

## 1.5. Pythonic Fidelity & The Pure Python Advantage

#### *Python exposes a high degree of mathematical and logical fidelity. TokenGate preserves it.*

TokenGate is built entirely using standard library and "pip" packages. It contains **zero CPython extensions**. It   
was deliberately designed to take full advantage of Python's dynamic object reference mechanisms and boolean   
flexibility. It can run in any standard Python environment (tested on 3.12 and 3.13) without requiring complex build   
steps or external compilers. 

**Architectural Consideration:** TokenGate intentionally uses layered abstractions and dynamic data structures to  
minimize boilerplate. This allows for zero-friction user adoption (a simple decorator), but it means the system leans  
heavily on Python's dynamic typing and object model. While this approach bypasses the rigid optimizations typically   
achieved via C-extensions, the architectural benefits, rapid development, deep telemetry, and unhindered logic—  
vastly outweigh the raw compute trade-offs. TokenGate proves that pure Python, when orchestrated correctly, is   
exceptionally powerful.

---

## 2. The Dual-Plane Architecture

TokenGate operates on a strict dual-plane architecture. By leveraging `asyncio` and `threading` in tandem—and   
using the `TaskToken` as the universal bridge between them—the system cleanly separates control logic from  
execution logic. 

This abstraction eliminates the complexities of manual thread management. The **Control Plane** acts as the   
high-speed router, while the **Execution Plane** acts as the heavy-lifting engine.

### The Control Plane: Async Orchestration

The Control Plane is built entirely on `asyncio`. Because it never executes blocking CPU tasks directly, it   
stays fast and instantly responsive. When a task is submitted via the decorator, it is immediately snapshotted,  
tokenized and placed into an async queue. The Control Plane's event loop then continuously monitors the   
queue, evaluating token metadata (e.g., weight, storage speed) to dynamically load-balance and route tasks   
to the appropriate execution pathways.

**Key Components of the Control Plane:**
*   **Token Pool:** Manages the strict lifecycle state transitions of tokens.
*   **Admission Gate:** The async loop that controls the flow of tasks into the system, filtering tokens.
*   **Affinity Policy:** The routing logic that determines which physical cores a token is allowed to utilize.

```python
class TokenPool:
    """Thread-safe registry and admission queue for task tokens."""
    def __init__(self):
        self.quarantine_mgr = None
        self.tokens: Dict[str, TaskToken] = {} # Maps token_id to TaskToken
        self._lock = threading.Lock()
        # ...

    def create_token(self, func, args, kwargs, operation_type, tags) -> TaskToken:
        """Create, register, and enqueue a token for later admission."""
        self.total_created += 1
        token_id = f"{operation_type}_{time.time_ns()}"

        # Capture metadata immediately to start the lifecycle clock
        metadata = TokenMetadata(
            operation_type=operation_type,
            created_at=time.time(),
            tags=tags or {}
        )

        token = TaskToken(token_id, func, args, kwargs, metadata)

        with self._lock:
            self.tokens[token_id] = token

        # Asyncio takes over routing from the 'WAITING' state onwards
        token.transition_state(TokenState.WAITING) 
        if self._event_loop:
            asyncio.run_coroutine_threadsafe(
                self._token_queue.put(token),
                self._event_loop
            )
        return token
```

> The Admission Gate: An unrestricted async loop that polls directly from the pool and routes   
> tokens to the execution plane.

```python
async def _admission_loop(self):
    """Continuously move eligible tokens from the pool into the worker queue.

    This loop does not apply throughput throttling. It performs killed-token
    filtering, forwards admitted tokens, and briefly backs off only after
    unexpected loop errors.
    """
    print("[GATE] Admission loop started - unrestricted flow")

    while self._active:
        try:
            # Get the next token directly from the pool
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

async def _admit_token(self, token: TaskToken):
    """Transition a token into the admitted state and enqueue it for execution."""
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
```

> Core Affinity Policy: How the Control Plane restricts token execution to specific physical  
> CPU cores based on computational weight.

```python
class CoreAffinityPolicy:
    """Builds and exposes per-weight core eligibility rules.

    The policy derives allowed-core chains from detected physical core count
    and preserves weight isolation rules where possible.
    """
    def __init__(self, num_cores: int):
        self.num_cores = num_cores
        self._build_preferences()

    def _build_preferences(self):
        """Construct per-weight core preference chains from available core count."""

        # Heavy can use ALL cores, prefers Core 1
        heavy_cores = list(range(1, self.num_cores + 1))

        # Medium starts at Core 2 (NEVER Core 1)
        medium_cores = list(range(2, self.num_cores + 1)) if self.num_cores >= 2 else []
        if not medium_cores:
            # Fallback for 1-core system (shouldn't happen but handle it)
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

        print(f"[AFFINITY] Policy for {self.num_cores} cores:")
        print(f"  Heavy:  {heavy_cores} (preferred: {heavy_cores[0]})")
        print(f"  Medium: {medium_cores} (preferred: {medium_cores[0] if medium_cores else 'N/A'})")
        print(f"  Light:  {light_cores} (preferred: {light_cores[0] if light_cores else 'N/A'})")
```
---


### The Execution Plane: Smart Workers (The Reactor Pattern)

The execution plane consists of a dynamic pool of worker threads. However, TokenGate departs from traditional   
Python threading by implementing a "Smart Worker" (Reactor) Pattern.

Instead of using "dumb" synchronous while True loops that blind the thread during execution, TokenGate's worker   
loops are actually asynchronous. They actively consume tokens from the async mailboxes and delegate the heavy,   
blocking payload to a standard thread pool via run_in_executor().

This is part of the secret to TokenGate's responsiveness: by executing synchronous code inside an async wrapper, the   
system yields the GIL during I/O and maintains constant telemetry. The worker can instantly report execution times, trigger   
retries upon failure, or respond to WebSocket kill commands without ever deadlocking.

> The Smart Worker Loop: Notice how the worker pulls from an async queue, yet handles deterministic core-pinning.

```python
# Workers continuously pull from their assigned mailbox and execute tokens as they arrive. Each   
# worker is pinned to a specific core based on the affinity policy, ensuring efficient execution   
# of CPU-bound tasks without contention.
async def _worker_loop(self, worker_idx: int, worker_id: str, core_id: int, local_i: int):
    """Continuously consume one mailbox and execute admitted tokens."""
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
        except Exception as e:
            print(f"[{worker_id}] Error: {e}")
            await asyncio.sleep(0.05)

    print(f"[{worker_id}] Stopped")
```

> **Note:** The execution plane is mostly asynchronous, this is needed, it relies on the call  
> `asyncio.get_event_loop().run_in_executor()` to execute the actual task functions   
> in a thread pool.

```python
# The event loop runner isolates the execution plane by generating the components   
# needed to operate the worker queue and admission gate.
def _run_event_loop(self):
    """Own and run the coordinator's background asyncio event loop.

    This method is executed on the dedicated loop thread. It creates the
    event loop, publishes the loop and async token queue into the global
    token pool, starts the worker queue and admission gate, optionally
    starts convergence monitoring, and then runs the loop until shutdown.
    """
    self._event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self._event_loop)

    # Tell the token pool about our event loop
    global_token_pool._event_loop = self._event_loop
    global_token_pool._token_queue = asyncio.Queue()

    # Start components
    self._event_loop.run_until_complete(self.worker_queue.start(self.num_executors))
    self._event_loop.run_until_complete(self.gate.start())

    # Start convergence if enabled
    if self.convergence:
        self._convergence_task = self._event_loop.create_task(self._convergence_loop())

    # Run forever
    try:
        self._event_loop.run_forever()
    finally:
        self._event_loop.close()
```

> The Execution Handoff: The precise moment the async Control Plane bridges into the synchronous Execution   
> Plane, capturing the result and triggering feedback loops (like the Overflow Guard).

```python
async def _execute_token(self, token: TaskToken, worker_id: str, core_id: int):
    """Execute one admitted token on its selected core path."""
    if not token.transition_state(TokenState.EXECUTING):
        return

    start_time = time.time()
    success = False

    try:
        # THE BRIDGE: Run the synchronous function in a background thread 
        # while keeping the async loop unblocked!
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: token.func(*token.args, **token.kwargs)
        )

        token.set_result(result)
        success = True

    except Exception as e:
        token.set_error(e) 

    finally:
        execution_duration = time.time() - start_time

        # 1. Record Execution Telemetry for the GUI
        if self.coordinator:
            # ... telemtry code ...
            
            # 2. OVERFLOW GUARD: Check for automatic retries on failure
            guard = self.coordinator.overflow_guard if hasattr(self.coordinator, 'overflow_guard') else None
            
            if guard and not success:
                should_retry = guard.should_retry(
                    token.token_id, execution_duration, success=False, operation_type=token.metadata.operation_type
                )
                if should_retry:
                    retry_token = guard.create_retry_token(token, execution_duration)

            # 3. GUARD HOUSE: Record complexity and time for anomaly detection
            if hasattr(self.coordinator, 'guard_house'):
                self.coordinator.guard_house.record_execution_result(...)
```

[Jump to top](#tokengate-architecture-deep-dive)

---

## 3. Intelligent Inspection & Byte-Level Optimization
#### *How TokenGate knows what a function will cost before it even runs.*    

**Byte-Level Code Inspection:** TokenGate makes use of code inspection for analysis and pattern detection.  
**Function Caching:** Functions are retried up to 3 times on failure to allow possible transient errors to resolve.  
**The Guard House & Spike Detection:** High memory usage triggers blocking and metrics enable informed response.

### Byte-Level Code Inspection

#### *Comprehensive metrics are the solution*

TokenGate uses metrics in every decision it makes. From token routing, to function analysis, to the convergence engine—  
metrics are the backbone of the system. 

One of the most advanced predictive metrics TokenGate uses is byte-level code inspection. The engine analyzes a   
function's bytecode (AST) to estimate its computational complexity before execution. This allows the system to   
make informed decisions about resource allocation without having to guess.

```python
# Data structure for storing code metrics extracted from bytecode analysis
@dataclass
class CodeMetrics:
    """Structured result of one code-object analysis pass."""
    func_name: str
    bytecode_length: int
    stack_size: int
    local_vars: int
    arg_count: int
    const_count: int
    const_footprint: int
    external_calls: List[str]
    loop_count: int
    branch_count: int
    complexity_score: float
    complexity_level: ComplexityLevel
    confidence: float
```

While code inspection handles predictive metrics, TokenGate uses Prometheus for real-time metrics. Prometheus acts as   
the "Jack of all trades" for dynamic worker scaling. Its internal gauges and counters are updated in real-time by the worker   
loops and the admission gate, feeding live data back into the system's Convergence Engine.

```python
class ThreadingMetrics:
    """Central Prometheus metrics registry for the execution system.

    Exposes runtime metrics for monitoring and provides the convergence
    layer with directly observed queue and worker-state signals.
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        """Initialize metric families in the provided or a new registry."""
        self.registry = registry or CollectorRegistry()

        # Task lifecycle counters
        self.tasks_submitted = Counter(
            'threading_tasks_submitted_total',
            'Total tasks submitted',
            ['operation_type'],
            registry=self.registry
        )
        
        # ... (Additional metrics like task_duration, queue_depth, and workers_busy) ...

        # Lock for thread-safe metric updates
        self._lock = threading.Lock()
```

### Smart Caching & Automatic Retries
#### *The system automatically retries failed tasks, bumping resource allocation each time.*   

The retry mechanism is designed to catch transient errors that may occur due to resource contention, temporary spikes in   
load, or other non-deterministic issues. Each executed task gets up to 3 chances to execute successfully before it is   
ultimately aborted.

Rather than just running the exact same failed token again, the Overflow Guard creates a specialized `BackupToken`.   
Notice how `calculate_next_allocation()` automatically scales up the memory/resource limits for the retry to give it a  
better chance of succeeding!

```python
@dataclass
class BackupToken:
    """Retry-tracking state for one failed original token."""
    original_token_id: str
    current_retry_count: int
    max_retries: int
    base_allocation_mb: int
    current_allocation_mb: int
    bump_percent: float
    complexity_level: ComplexityLevel
    created_at: float
    last_retry_at: float

    # Original function for recreation
    func: Callable
    args: tuple[Any, ...]
    kwargs: dict
    operation_type: Optional[str] = None

    def can_retry(self) -> bool:
        """Check if more retries allowed."""
        return self.current_retry_count < self.max_retries

    def calculate_next_allocation(self) -> int: # Backup tokens are optimized!
        """Calculate allocation for next retry."""
        return int(self.current_allocation_mb * (1 + self.bump_percent))
```

### The Guard House & Spike Detection  

#### *The system actively monitors for anomalous execution patterns and can automatically block or throttle tasks.*   

The Guard House is a defensive subsystem that collects execution metrics and applies anomaly detection rules. If a   
particular operation type suddenly starts consuming much more memory or takes much longer to execute than its   
historical average, the Guard House can trigger an automatic block on that operation type to prevent system overload.

```python
class GuardHouse:
    """Tracks method reputation and optionally blocks persistently unsafe methods.

    Guard House records post-execution outcomes, classifies method health,
    exposes developer-facing diagnostics, and can enforce pre-execution
    blocking when a method exceeds the configured danger threshold.
    """
    def __init__(self, auto_block_dangerous: bool = True):
        """
         Initialize Guard House.

         Args:
             auto_block_dangerous: Whether methods with sustained extreme failure
                 rates should be blocked before future execution attempts.
         """
        # Configuration
        self.auto_block_dangerous = auto_block_dangerous

        # Reputation tracking
        self.method_reputations: Dict[str, MethodReputation] = {}
        self._lock = threading.RLock()

        # Statistics
        self.total_methods_tracked = 0
        self.total_executions_monitored = 0
        self.monitoring_start_time = time.time()

        # Blocked methods (if auto_block enabled)
        self.blocked_methods: set = set()
```

To complement the Guard House, TokenGate utilizes a Complexity Spike Detector. This heuristic-based monitor   
compares real-time execution complexity against historical averages. When processes become anomalously   
erroneous or resource-intensive, the detector automatically quarantines the offending operation type. The   
`QuarantinedToken` preserves the exact arguments and state so developers can debug the specific input that   
caused the spike.

```python
# Tokens which end up quarantined are tracked and arguments remain intact.
class QuarantinedToken:
    """
    Record of a quarantined token.

    Preserves all information needed to replay or analyze.
    """
    token_id: str
    method_name: str
    operation_type: str

    # Complexity analysis
    predicted_complexity: float
    historical_avg_complexity: float
    deviation_percent: float

    # Preserved data
    args_summary: str  # Brief description
    args_blob: Optional[str]  # Full args (maybe huge)
    kwargs_summary: str
    kwargs_blob: Optional[str]

    # Metadata
    timestamp: float
    quarantine_reason: str
    admin_reviewed: bool = False
    admin_decision: Optional[str] = None
```

[Jump to top](#tokengate-architecture-deep-dive)

---

## 4. The Convergence Engine & Worker Affinity

#### *How the system dynamically shapes itself to the hardware and current load.*

> ***Bonus optimization at the end provides exclusive details into an "idle worker" trick.***

*   **Prometheus Metrics & Dynamic Convergence:** Polling system metrics to dynamically scale the worker pools.
*   **Layered Affinity Routing:** The internal queueing mechanics that map specific operation types directly.

### Prometheus Metrics & Dynamic Convergence

The Convergence Engine is an optional subsystem that continuously monitors the system's Prometheus metrics to   
make informed decisions about scaling the worker pool. By analyzing real-time data on queue depths, execution   
times, and worker utilization, the Convergence Engine can automatically adjust the number of active workers to   
optimize throughput and minimize latency.

Part of the convergence engine centers on the analysis of internal runtime metrics to assess true hardware pressure:  

```python 
def _analyze_single_core(
        self,
        core_id: int,
        prom_data: str,
        worker_pool
) -> CorePressure:
    """Build one core-pressure summary from current Prometheus metrics."""
    # Get queue depth (current gauge value)
    queue_depth = self._extract_gauge(
        prom_data,
        'threading_queue_depth',
        {'core_id': str(core_id)}
    )

    # Get worker utilization
    utilization = self._extract_gauge(
        prom_data,
        'threading_worker_utilization_percent',
        {'core_id': str(core_id)}
    )

    # Calculate queue wait p95 from histogram
    queue_wait_p95 = self._calculate_histogram_percentile(
        prom_data,
        'threading_queue_wait_seconds',
        {'core_id': str(core_id)},
        0.95
    )

    # Calculate average task duration
    avg_duration = self._calculate_histogram_average(
        prom_data,
        'threading_task_duration_seconds',
        {'core_id': str(core_id)}
    )

    # Assess pressure level
    pressure_level, recommended = self._assess_pressure(
        core_id,
        queue_depth,
        utilization,
        queue_wait_p95,
        worker_pool
    )

    return CorePressure(
        core_id=core_id,
        queue_depth=int(queue_depth) if queue_depth else 0,
        worker_utilization=utilization if utilization else 0.0,
        queue_wait_p95=queue_wait_p95,
        avg_task_duration=avg_duration,
        pressure_level=pressure_level,
        recommended_pattern=recommended
    )
```

### Layered Affinity Routing

The affinity routing system is designed to ensure that tasks are executed on the most appropriate CPU cores based on   
their computational weight. By analyzing the metadata of each token, the system can determine which cores are eligible  
for execution and route tasks accordingly. This layered approach allows for efficient resource utilization while maintaining   
strict isolation between different types of workloads.

The affinity queue is a robust platform packed into a concise pattern:

```python
class CoreAffinityQueue:
    """Policy and reporting layer for weight-based core affinity.

    This class classifies tokens, exposes the allowed core chain for each
    weight class, and records routing outcomes reported by the execution
    queue layer.

    It does not place tokens into mailboxes directly.
    """
    def __init__(self, topology, workers_per_core: int = 4):
        self.topology = topology
        self.workers_per_core = workers_per_core
        self.num_cores = topology.physical_cores

        # Build affinity policy
        self.policy = CoreAffinityPolicy(self.num_cores)

        # Simple counters (no CoreQueue objects!)
        self._affinity_counts = {
            core_id: {'heavy': 0, 'medium': 0, 'light': 0}
            for core_id in range(1, self.num_cores + 1)
        }

        # Metrics
        self.total_routed = 0
        self.routing_failures = 0
        self._routing_lock = threading.Lock()

    def classify_token_weight(self, token: TaskToken) -> TaskWeight:
        """Infer routing weight from token tags or operation type.

        Tag-based weight takes precedence. If no explicit weight tag is present,
        the operation type is inspected for heavy/light hints. Medium is the
        fallback class.
        """
        # Check tags first
        if 'weight' in token.metadata.tags: # Metadata guides every step
            weight_str = token.metadata.tags['weight'].lower()
            if weight_str == 'heavy':
                return TaskWeight.HEAVY
            elif weight_str == 'light':
                return TaskWeight.LIGHT
            else:
                return TaskWeight.MEDIUM

        # Check operation_type suffix
        op_type = token.metadata.operation_type.lower()
        if op_type.endswith('_heavy') or 'heavy' in op_type:
            return TaskWeight.HEAVY
        elif op_type.endswith('_light') or 'light' in op_type:
            return TaskWeight.LIGHT

        # Default to medium
        return TaskWeight.MEDIUM
```

### **Bonus Optimization: Defeating Core-0 Thrashing via Deep Idling**
#### *How the system stopped tasks from piling the GIL.*

This optimization centers around "idling pinned workers", and it is what removed the bulk of the GIL bottleneck when   
building this architecture.

In a standard setup, if worker threads rapidly poll a queue or block on a traditional synchronous `queue.get()`, they   
create a storm of lock contention. The OS scheduler sees this thrashing and, to optimize CPU cache, dumps all the   
threads onto Core 0.

Instead, TokenGate's worker loops are asynchronous. They pull from `asyncio.Queue` which allows them to yield control  
back to the event loop entirely while waiting for tasks. This puts the worker into a deep idle state. Because the workers  
are truly asleep, they are not fighting for the GIL. When tasks finally arrive, the OS scheduler naturally spreads them   
across the assigned physical cores instead of clumping them.

To manage this deep idle state cleanly, the aforementioned "Jack of all trades" Prometheus metrics explicitly track and set     
utilization gauges. By explicitly updating the gauge, the Convergence Engine knows exactly when a core is safely resting   
without having to wake it up to check.   

This is how that state is recorded in TokenGate:

```python
def update_worker_state(self, core_id: int, busy_count: int, idle_count: int):
    """Update busy/idle worker gauges and derived utilization for a core."""
    self.workers_busy.labels(core_id=str(core_id)).set(busy_count)
    self.workers_idle.labels(core_id=str(core_id)).set(idle_count)

    total = busy_count + idle_count
    if total > 0:
        utilization = (busy_count / total) * 100
        self.worker_utilization.labels(core_id=str(core_id)).set(utilization) # 'set()' is the key  
        # The 'set()' method is used to update the gauge with the new utilization value. 
        # Directly contacting Prometheus to set the idle utilization is how to keep workers "deep idle".
```

[Jump to top](#tokengate-architecture-deep-dive)

---

## 5. Token Lifecycle & Queue Mechanics
#### *The internal mechanics that keep the system flowing smoothly.*

*   **Token Lifecycle Management:** The strict state machine that governs token transitions from creation to completion.
*   **Async Mailbox Queues:** The internal routing system that ensures tokens are processed by the correct worker threads.

### Token Lifecycle Management

Tokens follow a strict state machine that governs their lifecycle. This ensures tasks are processed in an orderly, thread-safe   
fashion, allowing for precise control over their execution. The states include `CREATED`, `WAITING`, `ADMITTED`, `EXECUTING`,  
`COMPLETED`, `FAILED`, and `KILLED`. Each transition is carefully guarded by an internal lock (`_state_lock`) to ensure that   
the system can safely respond to asynchronous interruptions, like a user manually killing a token mid-flight, without   
causing race conditions.
```python
class TokenState(Enum):
    """Lifecycle states for a token-managed task.

    Tokens move from creation to admission, execution, and a terminal state.
    Terminal states are COMPLETED, FAILED, KILLED, and TIMEOUT.
    """
    CREATED = "created"
    WAITING = "waiting"
    ADMITTED = "admitted"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    TIMEOUT = "timeout"
```
```python
def transition_state(self, new_state: TokenState) -> bool:
    """Attempt a validated lifecycle transition.

    Transitions are guarded by an internal lock and only allowed when the
    requested state is valid for the token's current state. Lifecycle
    timestamps are updated on successful transitions, and an optional
    state-change callback is invoked after the lock is released.

    Returns:
        True if the transition succeeded, otherwise False.
    """
    cb = None
    old_state = None
    with self._state_lock:
        valid_transitions = {
            TokenState.CREATED: {TokenState.WAITING, TokenState.KILLED},
            TokenState.WAITING: {TokenState.ADMITTED, TokenState.KILLED, TokenState.TIMEOUT},
            TokenState.ADMITTED: {TokenState.EXECUTING, TokenState.KILLED, TokenState.TIMEOUT},
            TokenState.EXECUTING: {TokenState.COMPLETED, TokenState.FAILED, TokenState.KILLED, TokenState.TIMEOUT},
            TokenState.COMPLETED: set(),
            TokenState.FAILED: set(),
            TokenState.KILLED: set(),
            TokenState.TIMEOUT: set(),
        }

        if new_state not in valid_transitions.get(self.state, set()):
            return False

        old_state = self.state
        self.state = new_state
        cb = getattr(self, "on_state_change", None)

        # timestamps
        now = time.time()
        if new_state == TokenState.ADMITTED:
            self.metadata.admitted_at = now
        elif new_state == TokenState.EXECUTING:
            self.metadata.started_at = now
        elif new_state in {TokenState.COMPLETED, TokenState.FAILED, TokenState.KILLED, TokenState.TIMEOUT}:
            self.metadata.completed_at = now

    if cb:
        cb(self, old_state, new_state)

    return True
```

### Async Mailbox Queues

Tokens are routed through a system of async mailbox queues organized by core affinity. When a token is admitted,   
it is placed into the appropriate mailbox based on its computational weight. Worker threads are assigned to specific   
mailboxes, guaranteeing execution on the correct physical cores.

Crucially, this system implements backpressure via a hard cap (MAILBOX_MAX = 100). This prevents runaway memory  
usage (a common vulnerability in naive async queues) and acts as an inherent DoS safety mechanism.

```python
class CorePinnedStaggeredQueue(WorkerTaskQueue):
    """Mailbox execution queue with core-aware staggered routing.

    Tokens are classified by routing weight, restricted to valid core ranges,
    assigned a staggered global position, and then placed into a specific
    per-worker mailbox for execution.

    This layer performs actual mailbox placement. It is the execution-facing
    counterpart to the affinity policy layer.
    """

    def __init__(self, num_cores: int, workers_per_core: int, coordinator: Any):
        """
        Initialize the pinned staggered mailbox queue.

        Args:
            num_cores: Number of physical cores exposed to the routing model.
            workers_per_core: Number of mailbox workers created per core.
            coordinator: OperationsCoordinator reference used for history,
                retry, overflow, and Guard House callbacks.
        """
        super().__init__()
        self.coordinator = coordinator
        self.num_cores = num_cores
        self.workers_per_core = workers_per_core
        self.total_workers = num_cores * workers_per_core

        # ---- Metrics ----
        self.metrics = get_metrics()

        # ---- Mailboxes: one asyncio.Queue per worker (core_id, local_i) ----
        self.mailboxes: Dict[Tuple[int, int], asyncio.Queue] = {}

        # Least-loaded routing helpers
        self.worker_queue_sizes: Dict[int, int] = {i: 0 for i in range(self.total_workers)}

        self.worker_positions: dict[str, list] = {}

        # Routing helpers
        self.core_queue_depth: Dict[int, int] = {c: 0 for c in range(1, self.num_cores + 1)}
        self.core_busy: Dict[int, int] = {c: 0 for c in range(1, self.num_cores + 1)}

        # Capped mailbox length to prevent runaway memory (DOS safety)
        self.MAILBOX_MAX = 100 # Max tokens per worker mailbox

        self.core_patterns: Dict[int, int] = {}
        for core_id in range(1, num_cores + 1):
            self.core_patterns[core_id] = workers_per_core  # Default: all workers active

        self.coordinator = coordinator

        # Initialize stats
        self.total_executed = 0
        self.total_failed = 0

        # Core-to-worker mapping
        # Core 1: workers [0, 1, 2, 3]
        # Core 2: workers [4, 5, 6, 7]
        # etc.
        self.core_workers: Dict[int, List[int]] = {}
        for core_id in range(1, num_cores + 1):
            start_worker = (core_id - 1) * workers_per_core
            self.core_workers[core_id] = list(range(start_worker, start_worker + workers_per_core))

        # Position tracking by core
        # Each core tracks its next available position
        self.core_position_counters: Dict[int, int] = {}
        for core_id in range(1, num_cores + 1):
            self.core_position_counters[core_id] = (core_id - 1) * workers_per_core

        # State
        self._active = False
        self._execution_tasks = []
```

[Jump to top](#tokengate-architecture-deep-dive)

---

## 6. Surgical Control & Active DoS Defense
*The human-in-the-loop and automated defenses.*
*   **Live Operation Controls:** The backend mechanics allowing administrators to Pause, Drain, or Kill specific operations.
*   **Auto-Blocking:** Setting `auto_block_dangerous=True` to let the system actively defend against event bus flooding.

### Live Operation Controls for Surgical Intervention

The system provides administrators with the ability to control operations in real-time. This includes pausing the admission of   
new tokens of a specific type (allowing them to safely buffer in a holding area), draining existing queues, or killing specific   
errant tokens mid-flight. These controls are essential for managing unexpected spikes in load, debugging issues, or   
responding to security incidents without dropping overall system traffic.

Some of the code used for this is as follows:

```python
@app.route('/api/admin/pause', methods=['POST'])
@login_required
def admin_pause():
    """Pause admission."""
    if coordinator:
        coordinator.pause_admission()
        return jsonify({'status': 'paused'})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/resume', methods=['POST'])
@login_required
def admin_resume():
    """Resume admission."""
    if coordinator:
        coordinator.resume_admission()
        return jsonify({'status': 'resumed'})
    return jsonify({'error': 'No coordinator'}), 503


@app.route('/api/admin/kill_token', methods=['POST'])
@login_required
def admin_kill_token():
    """Kill specific token."""
    data = request.json
    token_id = data.get('token_id')
    reason = data.get('reason', 'admin_override')
    
    if coordinator:
        success = coordinator.kill_token(token_id, reason)
        return jsonify({'success': success, 'token_id': token_id})
    
    return jsonify({'error': 'No coordinator'}), 503
```

### Auto-Blocking

For heavy production workloads or public-facing integrations, it is highly recommended to enable TokenGate's automatic   
anomaly defense. This prevents system overload by automatically blocking repeatedly failing functions.

If a function surpasses a 90% failure rate over at least 10 executions (excluding safety retries), the Guard House flags it   
as an active threat. It will reject all future tokens of that type at the admission gate before they even touch the execution   
plane. This is the ultimate "Active DoS Defense" mechanism, designed to protect the event bus from being flooded by   
persistently failing or malicious tasks.

```python
def record_execution_result(
        self,
        method_name: str,
        operation_type: str,
        success: bool,
        execution_time: float,
        failure_type: Optional[str] = None,
        complexity_score: Optional[float] = None
):
    """
    Record post-execution outcome data and update method reputation.

    This is the primary post-execution integration point used to maintain
    health classification, failure-pattern counts, timing statistics, and
    complexity baselines.
    """
    with self._lock:
        # Create reputation if first time seeing this method
        if method_name not in self.method_reputations:
            self.method_reputations[method_name] = MethodReputation(
                method_name=method_name,
                operation_type=operation_type,
                first_seen_at=time.time()
            )
            self.total_methods_tracked += 1

        rep = self.method_reputations[method_name]

        # Update counts
        rep.total_attempts += 1
        self.total_executions_monitored += 1

        if success:
            rep.successful_completions += 1
        else:
            rep.failed_executions += 1

            # Track failure patterns
            if failure_type == 'timeout':
                rep.timeout_count += 1
            elif failure_type == 'error':
                rep.error_count += 1
            elif failure_type == 'quick_fail':
                rep.quick_failure_count += 1

        # Update timing
        rep.total_execution_time += execution_time
        rep.last_execution_at = time.time()

        if execution_time < rep.min_execution_time:
            rep.min_execution_time = execution_time
        if execution_time > rep.max_execution_time:
            rep.max_execution_time = execution_time

        # Update complexity (for spike detection)
        if complexity_score is not None:
            rep.total_complexity_score += complexity_score
            rep.avg_complexity_score = rep.total_complexity_score / rep.total_attempts

        # Update health status
        old_health = rep.health_status
        new_health = rep.update_health_status()

        # AUTO-BLOCK CHECK (if enabled)
        if self.auto_block_dangerous:
            failure_rate = rep.get_failure_rate()

            # Block if >90% failure rate and at least 10 attempts
            if failure_rate > 90.0 and rep.total_attempts >= 10:
                if method_name not in self.blocked_methods:
                    self.blocked_methods.add(method_name)
                    print(f"[GUARD_HOUSE] 🚫 AUTO-BLOCKED: {method_name}")
                    print(f"  Failure rate: {failure_rate:.1f}% ({rep.failed_executions}/{rep.total_attempts})")
                    print(f"  This method will be rejected on future calls")
```

[Jump to top](#tokengate-architecture-deep-dive)

---

## 7. Future Horizons: Distributed Compute

#### *The architectural endgame is seamless distributed computation.*

The core vision is: A developer provides a script with decorated functions, the system slots in and runs the code.  
A system like this would enable users to sell timeslots on their high end hardware and clients will purchase slots to lock in  
execution on that hardware. (This will be added after 1.0 in the long run.)

## Final Thoughts: 

While TokenGate is still in active development, it establishes a remarkably solid foundation. By enforcing architectural   
discipline and treating pure Python as a high-fidelity control surface, we have broken through the traditional boundaries   
of parallelism. I am excited to produce more features, deepen the architecture, and the eventual prospect of "distributed   
computing for everyone." 👍