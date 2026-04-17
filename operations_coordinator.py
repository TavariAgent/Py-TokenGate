# -*- coding: utf-8 -*-
# operations_coordinator.py
"""
Runtime orchestration for token-managed execution.

This module defines the main coordinator responsible for wiring the token pool,
admission gate, worker queue, convergence engine, and supporting safety/metrics
components into one running execution system.

The coordinator owns the background asyncio event loop used for admission and
control-plane tasks. Worker execution is delegated to downstream execution
components started from that loop.
"""

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, List

from .admission_gate import AdmissionGate
from .core_affinity_queue import CoreAffinityQueue
from .core_pinned_staggered_queue import CorePinnedStaggeredQueue
from .guard_house import GuardHouse
from .overflow_guard import OverflowGuard
from .prometheus_convergence import PrometheusConvergenceEngine
from .threading_metrics import get_metrics
from .token_system import global_token_pool
from .topology_detector import TopologyDetector


@dataclass
class ExecutionRecord:
    """Immutable summary of one completed token execution.

    Used for recent-execution inspection, UI display, and optional
    JSON history export.
    """
    token_id: str
    operation_type: str
    method_name: str
    success: bool
    execution_time: float
    timestamp: float
    core_id: int
    worker_id: str
    complexity_score: Optional[float] = None

    def to_dict(self) -> dict:
        """Return a plain dictionary representation of the execution record."""
        return asdict(self)

class WorkerPoolInterface:
    """Minimal worker-pool adapter exposed to the convergence engine.

    This wrapper provides pool size information and pattern-control hooks
    without exposing the full worker queue implementation.
    """
    def __init__(self, worker_queue: 'CorePinnedStaggeredQueue'):
        self.worker_queue = worker_queue
        self.num_cores = worker_queue.num_cores
        self.workers_per_core = worker_queue.workers_per_core
        self.num_workers = worker_queue.total_workers
        self.set_pattern = worker_queue.set_core_pattern

    def set_core_pattern(self, core_id: int, pattern: int):
        """Update the active worker pattern for a core via the worker queue."""
        self.worker_queue.set_core_pattern(core_id, pattern)

    def get_pool_stats(self) -> dict:
        """Return worker-pool counts needed by convergence analysis."""
        return {
            'total_workers': self.num_workers,
            'num_cores': self.num_cores,
            'workers_per_core': self.workers_per_core
        }

class OperationsCoordinator:
    """Owns runtime startup, component wiring, and orderly shutdown.

    The coordinator assembles the token pool, admission gate, worker queue,
    convergence engine, safety components, and metrics into a single running
    execution system.

    It owns the background asyncio event loop used for admission and control
    tasks, and it starts the downstream worker/execution pipeline from that loop.
    """
    def __init__(
            self,
            workers_per_core: int = 4, # <- !!! THIS IS NOT ADJUSTABLE !!! (You can either disable convergence or leave that alone!)
            enable_convergence: bool = True,
            convergence_verbose: bool = False, # <- !!! THIS IS NOT COMPATIBLE WITH REPL !!!
            base_memory_budget_mb: int = 65,
            num_executors: int = 4,
            auto_block_dangerous: bool = False,
    ):
        """Initialize the coordinator and construct all runtime components.

        Args:
            workers_per_core: Target mailbox workers to create per detected
                physical core.
            enable_convergence: Whether to start adaptive convergence monitoring.
            convergence_verbose: Whether convergence decisions should be printed.
            base_memory_budget_mb: Base memory budget used by the overflow guard.
            num_executors: Number of executor-driving async tasks to start in the
                worker queue layer.
            auto_block_dangerous: Whether Guard House should automatically block
                dangerous methods when enough reputation data exists.

        Notes:
            Component construction happens here, but the runtime is not started
            until ``start()`` is called.
        """
        print("=" * 70)
        print("Operations Coordinator - Initializing...")
        print("=" * 70)
        print()

        # Configuration
        self.workers_per_core = workers_per_core
        self.enable_convergence = enable_convergence
        self.num_executors = num_executors
        self.quarantine_mgr = None
        self.spike_detector = None

        # Detect topology
        print("Detecting CPU topology...")
        detector = TopologyDetector()
        self.topology = detector.detect()
        detector.print_topology(self.topology)
        print()

        # Create foundation components
        print("Creating foundation components...")

        # Overflow guard (memory protection)
        self.overflow_guard = OverflowGuard(base_budget_mb=base_memory_budget_mb)

        # Guard House (method reputation tracking)
        self.guard_house = GuardHouse(auto_block_dangerous=auto_block_dangerous)

        # Core affinity policy (routing rules)
        self.affinity_queue = CoreAffinityQueue(self.topology, workers_per_core)

        # Metrics
        self.metrics = get_metrics()

        self.recent_executions = deque(maxlen=95) # ← Tune for micro performance gains
        self._executions_lock = threading.RLock()  # ← RLock for safety!

        print("Overflow guard initialized")
        print("Guard House initialized")
        print("Core affinity policy created")
        print()

        # Create execution pipeline
        print("Building execution pipeline...")

        # Worker queue - does routing AND execution
        from .core_pinned_staggered_queue import CorePinnedStaggeredQueue
        self.worker_queue = CorePinnedStaggeredQueue(
            num_cores=self.topology.logical_cores,
            workers_per_core=self.workers_per_core,
            coordinator=self
        )

        # Worker pool interface - for convergence control
        self.worker_pool = WorkerPoolInterface(self.worker_queue)

        # Admission gate - pure pass-through
        self.gate = AdmissionGate(
            token_pool=global_token_pool,
            worker_queue=self.worker_queue,
            worker_pool=self.worker_pool,
        )

        print("Worker queue created")
        print("Admission gate configured")
        print()

        # Convergence engine (optional)
        self.convergence: Optional[PrometheusConvergenceEngine] = None
        if enable_convergence:
            print("Configuring convergence engine...")
            self.convergence = PrometheusConvergenceEngine(
                topology=self.topology,
                queue_wait_threshold=1.0,
                utilization_high=80.0,
                utilization_low=40.0,
                queue_depth_factor=2,
                verbose=convergence_verbose
            )
            print("Prometheus convergence enabled")
            print()

        # State
        self._active = False
        self._convergence_task: Optional[asyncio.Task] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        print("Operations Coordinator ready!")
        print(f"  Cores: {self.topology.physical_cores}")
        print(f"  Workers per core: {workers_per_core}")
        print(f"  Total workers: {self.topology.physical_cores * workers_per_core}")
        print(f"  Convergence: {'ENABLED' if enable_convergence else 'disabled'}")
        print("=" * 70)
        print()

    def print_guard_house_dashboard(self):
        """Print the current Guard House heatmap dashboard."""
        self.guard_house.print_heatmap()

    def get_guard_house_stats(self):
        """Return Guard House summary statistics."""
        return self.guard_house.get_stats()

    def record_execution(self, record: ExecutionRecord):
        """Append a completed execution record to the recent-history buffer.

        This operation is protected by an internal re-entrant lock.
        """
        with self._executions_lock:
            self.recent_executions.append(record)

    def get_recent_executions(self, limit: int = 50) -> List[dict]:
        """Return up to ``limit`` recent execution records, newest first."""
        with self._executions_lock:
            # Convert to list, take last N, reverse for newest-first
            recent = list(self.recent_executions)[-limit:]
            return [rec.to_dict() for rec in reversed(recent)]

    def dump_execution_history(self, filepath: Optional[Path] = None) -> str:
        """Write the current recent-execution buffer to a JSON file.

        If no path is provided, a timestamped filename is generated in the
        current working directory.

        Returns:
            The path written, as a string.
        """
        if filepath is None:
            timestamp = int(time.time())
            filepath = Path(f'execution_history_{timestamp}.json')

        with self._executions_lock:
            data = {
                'timestamp': time.time(),
                'total_executions': len(self.recent_executions),
                'executions': [rec.to_dict() for rec in self.recent_executions]
            }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"[COORDINATOR] Execution history dumped to: {filepath}")
        return str(filepath)

    def start(self):
        """Start the coordinator runtime and initialize the control plane.

        Startup performs the following steps:

        1. Mark the coordinator active.
        2. Publish shared safety components into the global token pool.
        3. Start the background event-loop thread.
        4. Initialize the worker queue and admission gate on that loop.
        5. Start convergence monitoring when enabled.

        This method returns after the event loop has been created and the
        startup sequence has been dispatched.
        """
        if self._active:
            print("Operations Coordinator already running!")
            return

        print("Starting Operations Coordinator...")
        print()

        self._active = True

        global_token_pool._guard_house = self.guard_house
        global_token_pool._spike_detector = self.spike_detector
        global_token_pool._quarantine_mgr = self.quarantine_mgr

        # Start event loop in background thread
        self._loop_thread = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="Coordinator-EventLoop"
        )
        self._loop_thread.start()

        # Wait for loop to be ready
        while self._event_loop is None:
            time.sleep(0.01)

        print("Event loop started")
        print("Worker queue started")
        print("Admission gate started")

        if self.convergence:
            print("Convergence monitoring started")

        print()
        print("Operations Coordinator started successfully!")
        print()

    def stop(self):
        """Stop the coordinator and shut down runtime components in order.

        Shutdown proceeds in reverse dependency order:

        1. Stop convergence monitoring.
        2. Stop the admission gate.
        3. Stop the worker queue.
        4. Stop the background event loop.
        5. Join the loop thread before returning.

        This method is intended to provide a graceful shutdown path for the
        control plane and worker pipeline.
        """
        if not self._active:
            return

        print()
        print("Stopping Operations Coordinator...")
        print()

        self._active = False


        # Stop convergence first
        if self._convergence_task:
            asyncio.run_coroutine_threadsafe(
                self._stop_convergence(),
                self._event_loop
            ).result(timeout=5.0)

        # Stop gate and workers
        asyncio.run_coroutine_threadsafe(
            self._stop_execution(),
            self._event_loop
        ).result(timeout=5.0)

        # Stop event loop
        self._event_loop.call_soon_threadsafe(self._event_loop.stop) # Type: Ignore safe tuple

        if self._loop_thread:
            self._loop_thread.join(timeout=5.0)

        print("All components stopped")
        print()
        print("Operations shutdown complete!")
        print()

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

    async def _convergence_loop(self):
        """Periodically analyze worker pressure and apply pattern adjustments."""
        while self._active:
            try:
                await asyncio.sleep(5.0)

                # Analyze cores
                core_pressures = self.convergence.analyze_cores(self.worker_pool)

                # Get adjustments
                adjustments = self.convergence.recommend_adjustments(core_pressures)

                if adjustments:
                    if self.convergence.verbose:
                        print(f"[CONVERGENCE] Applying {len(adjustments)} pattern adjustments...")
                    for core_id, new_pattern in adjustments.items():
                        self.convergence.apply_pattern(core_id, new_pattern, self.worker_pool)

            except Exception as e:
                print(f"[CONVERGENCE] Error: {e}")
                await asyncio.sleep(5.0)

    async def _stop_convergence(self):
        """Cancel and await the background convergence task, if running."""
        if self._convergence_task:
            self._convergence_task.cancel()
            try:
                await self._convergence_task
            except asyncio.CancelledError:
                pass

    async def _stop_execution(self):
        """Stop the admission gate and worker queue."""
        await self.gate.stop()
        await self.worker_queue.stop()

    # ========================================================================
    # Admin-facing API
    # ========================================================================

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
            'token_pool': global_token_pool.get_stats(),
            'admission_gate': self.gate.get_stats(),
            'worker_queue': self.worker_queue.get_stats(),
            'affinity_distribution': self.affinity_queue.get_affinity_report(),
            'convergence': self.convergence.get_convergence_status() if self.convergence else None
        }

    def get_affinity_report(self):
        """Print the current core-affinity distribution report."""
        self.affinity_queue.print_affinity_report()

    def kill_token(self, token_id: str, reason: str = "admin_override") -> bool:
        """Kill a token by id via the global token pool."""
        return global_token_pool.kill_token(token_id, reason)

    def kill_all_by_operation(self, operation_type: str, reason: str = "admin_bulk_kill") -> int:
        """Kill all tokens matching an operation type."""
        return global_token_pool.kill_all_by_operation(operation_type, reason)

    def pause_admission(self):
        """Pause token admission while continuing to accept submissions."""
        global_token_pool.pause()

    def resume_admission(self):
        """Resume token admission from the global token pool."""
        global_token_pool.resume()

    def drain_pool(self) -> int:
        """Kill all tokens still waiting for admission and return the count."""
        return global_token_pool.drain()

    def drain_operation(self, operation_type: str, reason: str = "admin_per-token_drain") -> int:
        """Drain a specific token"""
        return global_token_pool.drain(operation_type, reason)

    def pause_operation(self, operation_type: str, reason: str = "admin_per-token_pause") -> int:
        """Pause a specific token"""
        return global_token_pool.pause(operation_type, reason)

    def resume_operation(self, operation_type: str, reason: str = "admin_per-token_resume") -> int:
        """Resume a specific token"""
        return global_token_pool.resume(operation_type, reason)


# ============================================================================
# Global singleton for decorator usage
# ============================================================================

_global_coordinator: Optional[OperationsCoordinator] = None
_coordinator_lock = threading.Lock()


def get_global_coordinator() -> OperationsCoordinator:
    """Return the process-global coordinator, creating and starting it if needed.

    This function exists primarily to support decorator-driven submission paths
    that need a running coordinator without explicit manual wiring.
    """
    global _global_coordinator

    if _global_coordinator is None:
        with _coordinator_lock:
            if _global_coordinator is None:
                _global_coordinator = OperationsCoordinator()
                _global_coordinator.start()

    return _global_coordinator


def set_global_coordinator(coordinator: OperationsCoordinator):
    """Replace the process-global coordinator instance.

    Intended for tests or for applications that construct the coordinator
    manually with custom configuration.
    """
    global _global_coordinator

    with _coordinator_lock:
        _global_coordinator = coordinator