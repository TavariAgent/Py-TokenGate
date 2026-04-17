# -*- coding: utf-8 -*-
# core_pinned_staggered_queue.py
"""
Core-pinned mailbox routing with staggered position assignment.

This module maps token weight classes onto valid core ranges and assigns
monotonic staggered positions that resolve to specific per-worker mailboxes.

Routing happens before mailbox placement:
    HEAVY  -> Core 1 range
    MEDIUM -> Core 2+ range
    LIGHT  -> Core 3+ range

This keeps mailbox placement aligned with the configured affinity policy.
"""

import asyncio
import time
from functools import partial
from typing import Dict, List, Tuple, Any

from .threading_metrics import get_metrics
from .token_system import TaskToken, TokenState
from .admission_gate import WorkerTaskQueue
from .core_affinity_queue import TaskWeight



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
        self.result_verbose = False
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
        self.MAILBOX_MAX = 75 # Max tokens per worker mailbox

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

        print(f"[CORE_PINNED_QUEUE] Initialized:")
        print(f"  Cores: {num_cores}")
        print(f"  Workers per core: {workers_per_core}")
        print(f"  Total workers: {self.total_workers}")
        print(f"  Core-worker mapping:")
        for core_id, workers in self.core_workers.items():
            print(f"    Core {core_id}: Workers {workers}")

    async def _mailbox_monitor_loop(self):
        """Periodically sample mailbox depths for active workers on each core."""
        while self._active:
            await asyncio.sleep(1.0)
            for core_id in range(1, self.num_cores + 1):
                active = int(self.core_patterns.get(core_id, self.workers_per_core))
                active = max(1, min(self.workers_per_core, active))
                qs = [self.mailboxes[(core_id, i)].qsize() for i in range(active)]

    def _worker_index(self, core_id: int, local_i: int) -> int:
        """Return the flattened worker index for a core/local-worker pair."""
        return (core_id - 1) * self.workers_per_core + local_i

    def _choose_local_worker_least_loaded(self, core_id: int) -> int:
        """Return the active local worker with the smallest current mailbox depth."""
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

    def set_core_pattern(self, core_id: int, pattern_value: int):
        """Set the number of active mailbox workers for a core."""
        self.core_patterns[core_id] = int(pattern_value)
        self.metrics.update_pattern(core_id, int(pattern_value))

    # optional alias for callers that use set_pattern
    def set_pattern(self, core_id: int, pattern_value: int):
        """Alias for set_core_pattern()."""

        self.set_core_pattern(core_id, pattern_value)

    async def _execute_token(self, token: TaskToken, worker_id: str, core_id: int):
        """Execute one admitted token on its already-selected core path.

        This method performs the lifecycle transition to EXECUTING, runs the
        wrapped callable through the executor-backed path, stores the result or
        error on the token, records execution history for the coordinator, and
        triggers retry/Guard House hooks when configured.
        """
        # Transition to executing
        if not token.transition_state(TokenState.EXECUTING):
            print(f"[{worker_id.upper()}] Failed to transition {token.token_id}")
            return

        start_time = time.time()
        success = False

        try:
            loop = asyncio.get_running_loop()
            bound_func = partial(token.func, *token.args, **token.kwargs)
            result = await loop.run_in_executor(None, bound_func)
            token.set_result(result)
            self.total_executed += 1
            success = True

            print(f"[{worker_id.upper()}] ✓ Completed {token.token_id}")

        except Exception as e:
            # Failed!
            token.set_error(e)
            self.total_failed += 1
            if self.result_verbose:
                print(f"[{worker_id.upper()}] ✗ Failed {token.token_id}: {e}")

        finally:
            execution_duration = time.time() - start_time

            # RECORD EXECUTION FOR GUI
            if self.coordinator:
                from .operations_coordinator import ExecutionRecord

                record = ExecutionRecord(
                    token_id=token.token_id,
                    operation_type=token.metadata.operation_type,
                    method_name=token.func.__name__,
                    success=success,
                    execution_time=execution_duration,
                    timestamp=time.time(),
                    core_id=core_id,
                    worker_id=worker_id,
                    complexity_score=token.metadata.tags.get('complexity_score')
                )

                self.coordinator.record_execution(record)
            else:
                print(f"[DEBUG] No coordinator to record to!")

                # CHECK FOR RETRY (if coordinator and overflow guard available)
            guard = None
            if self.coordinator and hasattr(self.coordinator, 'overflow_guard'):
                guard = self.coordinator.overflow_guard

            if guard and self.coordinator:
                # Determine failure type
                failure_type = None

                if not success:
                    if execution_duration > 60.0:
                        failure_type = 'timeout'
                    elif execution_duration < 10.0:
                        failure_type = 'quick_fail'
                    else:
                        failure_type = 'error'
                else:
                    # Record success (for retry tracking)
                    guard.record_success(token.token_id, execution_duration)

                # Check if we should retry (only for failures)
                should_retry = False
                if not success:
                    should_retry = guard.should_retry(
                        token.token_id,
                        execution_duration,
                        success=False,
                        operation_type=token.metadata.operation_type
                    )

                    guard = None
                    if self.coordinator and hasattr(self.coordinator, 'overflow_guard'):
                        guard = self.coordinator.overflow_guard

                    # Create a retry token if needed
                    if should_retry:
                        print(f"[{worker_id.upper()}] Triggering retry for {token.token_id}")
                        retry_token = guard.create_retry_token(token, execution_duration)

                        if retry_token:
                            print(f"[{worker_id.upper()}] Created retry: {retry_token.token_id}")
                        else:
                            print(f"[{worker_id.upper()}] Retries exhausted for {token.token_id}")

                # Record result in Guard House
                if hasattr(self.coordinator, 'guard_house'):
                    self.coordinator.guard_house.record_execution_result(
                        method_name=token.func.__name__,
                        operation_type=token.metadata.operation_type,
                        success=success,
                        execution_time=execution_duration,
                        failure_type=failure_type,
                        complexity_score=token.metadata.tags.get('complexity_score')
                    )

    async def _execute_token_with_metrics(self, token: "TaskToken", worker_id: str, core_id: int):
        """Execute one token while updating worker-state and outcome metrics."""
        op_type = token.metadata.tags.get("operation_type", "unknown")

        t0 = time.perf_counter()
        try:
            await self._execute_token(token, worker_id, core_id)

            self.metrics.record_task_completion(op_type, core_id, time.perf_counter() - t0)
        except Exception:
            self.metrics.record_task_failure(op_type, core_id)
            raise

    def get_core_for_weight(self, weight: TaskWeight) -> List[int]:
        """Return the eligible core range for a routing weight.

        The returned list follows the active affinity policy, with fallback to
        lower-indexed cores on small-core systems when necessary.
        """
        if weight == TaskWeight.HEAVY:
            # Heavy only core 1
            return [1]

        elif weight == TaskWeight.MEDIUM:
            # Cores 2+ (never Core 1)
            if self.num_cores >= 2: # Medium core 2+
                return list(range(2, self.num_cores + 1))
            else:
                # Fallback for single-core systems
                return [1]

        else:
            # Light core 3+ (never Cores 1-2)
            if self.num_cores >= 3:
                return list(range(3, self.num_cores + 1))
            elif self.num_cores >= 2:
                # Fallback: use Core 2+ if only 2 cores
                return list(range(2, self.num_cores + 1))
            else:
                # Fallback for single-core
                return [1]

    @staticmethod
    def classify_token_weight(token: TaskToken) -> TaskWeight:
        """Infer routing weight from token tags or operation-type naming.

        Explicit weight tags take precedence over operation-type heuristics.
        """
        # Check tags first
        if 'weight' in token.metadata.tags:
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

    def choose_worker_for_core(self, core_id: int) -> int:
        """Choose the least-loaded active worker slot for the given core."""
        active = self.core_patterns.get(core_id, self.workers_per_core)  # pattern lock (2/3/4)
        # pick least-loaded among active workers
        best_i = 0
        best_len = 10 ** 18
        base = (core_id - 1) * self.workers_per_core
        for i in range(active):
            wid = base + i
            qlen = self.worker_queue_sizes[wid]  # track counts (fast)
            if qlen < best_len:
                best_len = qlen
                best_i = i
        return best_i

    def assign_worker_positions(self, worker_id: str, worker_index: int, core_id: int):
        """Precompute the staggered global positions owned by one worker."""
        positions = []

        # Base position for this worker
        base = (core_id - 1) * self.workers_per_core + (worker_index % self.workers_per_core)

        # Pre-allocate positions (every total_workers)
        for cycle in range(100):  # 1000 cycles
            positions.append(base + (cycle * self.total_workers))

        self.worker_positions[worker_id] = positions

        print(f"[CORE_PINNED] Worker {worker_id} (Core {core_id}): {positions[:5]}... (every {self.total_workers})")

    def assign_position_for_token(self, token: TaskToken) -> int:
        """Assign a staggered global route position for a token.

        The assigned position respects token weight, valid-core range, current
        per-core pattern, and each core's next position counter.
        """

        # Classify weight
        weight = self.classify_token_weight(token)

        # Get valid cores for this weight
        valid_cores = self.get_core_for_weight(weight)

        # Position loading is front bound and assignment is ranged for valid tasks
        chosen_core = min(valid_cores, key=lambda c: self.core_position_counters[c])

        # Get the current pattern for this core
        active_workers = self.core_patterns.get(chosen_core, self.workers_per_core)

        # Calculate position using ONLY active workers
        base_position = (chosen_core - 1) * self.workers_per_core
        position_in_cycle = self.core_position_counters[chosen_core] % active_workers

        # Calculate actual position
        cycle_number = self.core_position_counters[chosen_core] // active_workers
        position = base_position + position_in_cycle + (cycle_number * self.total_workers)

        # Increment counter
        self.core_position_counters[chosen_core] += 1

        if self.result_verbose:
            print(f"[ROUTING] Token {token.token_id} ({weight.value}) → Pos {position} (Core {chosen_core}, Pattern {active_workers})")
        return position

    async def put(self, token: "TaskToken"):
        """Route a token to a mailbox and apply bounded enqueue backpressure.

        The token is tagged with enqueue timing metadata, assigned a route
        position, resolved to a core/local-worker mailbox, and enqueued without
        dropping work. If the chosen mailbox is full, the queue retries with the
        least-loaded active worker and then awaits capacity if necessary.
        """
        # Tag + enqueue timestamp
        op_type = getattr(token, "operation_type", None) or token.metadata.tags.get("operation_type", "unknown")
        token.metadata.tags["operation_type"] = op_type
        token.metadata.tags["enqueued_at"] = time.perf_counter()

        self.metrics.record_task_submission(op_type)

        # Keep your routing address (trace)
        position = self.assign_position_for_token(token)
        token.metadata.tags["route_position"] = position

        # Derive target from position
        worker_index = position % self.total_workers
        core_id = (worker_index // self.workers_per_core) + 1
        local_i = worker_index % self.workers_per_core

        # Pattern lock: only active locals are eligible
        active = int(self.core_patterns.get(core_id, self.workers_per_core))
        active = max(1, min(self.workers_per_core, active))
        if local_i >= active:
            local_i = self._choose_local_worker_least_loaded(core_id)

        q = self.mailboxes[(core_id, local_i)]

        # Enqueue (fast path)
        try:
            q.put_nowait(token)
        except asyncio.QueueFull:
            # Soft fallback: try least-loaded active worker again (queues can fill unevenly)
            local_i = self._choose_local_worker_least_loaded(core_id)
            q = self.mailboxes[(core_id, local_i)]
            # If still full, await a slot (true backpressure) instead of dropping
            await q.put(token)

        # Per-core depth gauge
        self.core_queue_depth[core_id] += 1
        self.metrics.update_queue_depth(core_id, self.core_queue_depth[core_id])

    async def start(self, num_executors: int = 4):
        """Create per-worker mailboxes and start all worker-loop tasks.

        I used the inherited start(...) method as a typed configuration
        handoff point. That let the coordinator pass startup configuration
        across module boundaries without needing a separate setter or tighter
        coupling to the concrete queue implementation.

        This method is idempotent while the queue is already active.
        """
        if self._active:
            return

        self._active = True
        self._execution_tasks = []

        # Create per-worker mailboxes (loop context safe)
        for core_id in range(1, self.num_cores + 1):
            for local_i in range(self.workers_per_core):
                key = (core_id, local_i)
                if key not in self.mailboxes:
                    self.mailboxes[key] = asyncio.Queue(maxsize=self.MAILBOX_MAX)

        print(f"[CORE_PINNED_QUEUE] Starting {self.total_workers} mailbox workers...")
        for worker_idx in range(self.total_workers):
            core_id = (worker_idx // self.workers_per_core) + 1
            local_i = worker_idx % self.workers_per_core
            worker_id = f"worker_{worker_idx}_core_{core_id}"

            task = asyncio.create_task(
                self._worker_loop(worker_idx, worker_id, core_id, local_i),
                name=worker_id,
            )
            self._execution_tasks.append(task)

        print(f"[CORE_PINNED_QUEUE] Started {self.total_workers} workers across {self.num_cores} cores")

    async def _worker_loop(self, worker_idx: int, worker_id: str, core_id: int, local_i: int): # DO NOT REMOVE "worker_idx"!
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

    async def stop(self):
        """Cancel worker tasks, stop mailbox consumption, and await shutdown."""
        if not self._active:
            return

        print("[CORE_PINNED_QUEUE] Stopping all workers...")

        self._active = False

        # Cancel all worker tasks
        for task in self._execution_tasks:
            task.cancel()

        # Wait for them to finish
        await asyncio.gather(*self._execution_tasks, return_exceptions=True)

        self._execution_tasks = []

        print("[CORE_PINNED_QUEUE] All workers stopped")

    def get_stats(self) -> dict:
        """Return queue configuration, counters, and per-core position state."""
        return {
            'num_cores': self.num_cores,
            'workers_per_core': self.workers_per_core,
            'total_workers': self.total_workers,
            'total_executed': self.total_executed,
            'total_failed': self.total_failed,
            'core_position_counters': dict(self.core_position_counters)
        }