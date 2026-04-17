# -*- coding: utf-8 -*-
# threading_metrics.py
"""
Prometheus metrics for token-managed execution.

Provides counters, gauges, and histograms for task lifecycle events,
queue behavior, worker state, and convergence-related pattern changes.

These metrics support both external monitoring and internal runtime
adaptation based on directly observed system behavior.
"""

import threading
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry


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

        self.tasks_completed = Counter(
            'threading_tasks_completed_total',
            'Total tasks completed successfully',
            ['operation_type', 'core_id'],
            registry=self.registry
        )

        self.tasks_failed = Counter(
            'threading_tasks_failed_total',
            'Total tasks that failed',
            ['operation_type', 'core_id'],
            registry=self.registry
        )

        # Task duration histogram
        # Buckets: 1ms, 10ms, 50ms, 100ms, 500ms, 1s, 2s, 5s, 10s
        self.task_duration = Histogram(
            'threading_task_duration_seconds',
            'Task execution time in seconds',
            ['operation_type', 'core_id'],
            buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
            registry=self.registry
        )

        # Queue wait time histogram
        self.queue_wait_time = Histogram(
            'threading_queue_wait_seconds',
            'Time task spent waiting in queue',
            ['core_id'],
            buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0),
            registry=self.registry
        )

        # Current queue depth per core
        self.queue_depth = Gauge(
            'threading_queue_depth',
            'Current number of tasks in queue',
            ['core_id'],
            registry=self.registry
        )

        # Workers state
        self.workers_busy = Gauge(
            'threading_workers_busy',
            'Number of workers currently executing tasks',
            ['core_id'],
            registry=self.registry
        )

        self.workers_idle = Gauge(
            'threading_workers_idle',
            'Number of workers waiting for tasks',
            ['core_id'],
            registry=self.registry
        )

        # Current worker pattern (2=HEAVY, 3=MEDIUM, 4=LIGHT)
        self.worker_pattern = Gauge(
            'threading_worker_pattern',
            'Current worker pattern for core (2=HEAVY, 3=MEDIUM, 4=LIGHT)',
            ['core_id'],
            registry=self.registry
        )

        # Worker utilization percentage
        self.worker_utilization = Gauge(
            'threading_worker_utilization_percent',
            'Percentage of workers busy on this core',
            ['core_id'],
            registry=self.registry
        )

        # Convergence events
        self.convergence_changes = Counter(
            'threading_convergence_pattern_changes_total',
            'Total number of pattern changes due to convergence',
            ['core_id', 'from_pattern', 'to_pattern'],
            registry=self.registry
        )

        # Lock for thread-safe metric updates
        self._lock = threading.Lock()

    def record_task_submission(self, operation_type: str):
        """Increment the submitted-task counter for an operation type."""
        self.tasks_submitted.labels(operation_type=operation_type).inc()

    def record_task_completion(self, operation_type: str, core_id: int, duration: float):
        """Record successful completion and observe execution duration."""
        self.tasks_completed.labels(operation_type=operation_type, core_id=str(core_id)).inc()
        self.task_duration.labels(operation_type=operation_type, core_id=str(core_id)).observe(duration)

    def record_task_failure(self, operation_type: str, core_id: int):
        """Increment the failed-task counter for an operation/core pair."""
        self.tasks_failed.labels(operation_type=operation_type, core_id=str(core_id)).inc()

    def record_queue_wait(self, core_id: int, wait_time: float):
        """Observe queue wait time for a core."""
        self.queue_wait_time.labels(core_id=str(core_id)).observe(wait_time)

    def update_queue_depth(self, core_id: int, depth: int):
        """Set the current queue depth gauge for a core."""
        self.queue_depth.labels(core_id=str(core_id)).set(depth)

    def update_worker_state(self, core_id: int, busy_count: int, idle_count: int):
        """Update busy/idle worker gauges and derived utilization for a core."""
        self.workers_busy.labels(core_id=str(core_id)).set(busy_count)
        self.workers_idle.labels(core_id=str(core_id)).set(idle_count)

        total = busy_count + idle_count
        if total > 0:
            utilization = (busy_count / total) * 100
            self.worker_utilization.labels(core_id=str(core_id)).set(utilization)

    def update_pattern(self, core_id: int, pattern_value: int):
        """Set the active worker-pattern gauge for a core."""
        self.worker_pattern.labels(core_id=str(core_id)).set(pattern_value)

    def record_convergence_change(self, core_id: int, from_pattern: int, to_pattern: int):
        """Increment the convergence-triggered pattern-change counter."""
        self.convergence_changes.labels(
            core_id=str(core_id),
            from_pattern=str(from_pattern),
            to_pattern=str(to_pattern)
        ).inc()

    def get_registry(self) -> CollectorRegistry:
        """Return the Prometheus registry used by this metrics instance."""
        return self.registry


# Global singleton instance
_global_metrics: Optional[ThreadingMetrics] = None
_global_lock = threading.Lock()


def get_metrics() -> ThreadingMetrics | None:
    """Return the process-global metrics instance, creating it if needed."""
    global _global_metrics

    if _global_metrics is None:
        with _global_lock:
            if _global_metrics is None:
                _global_metrics = ThreadingMetrics()

    return _global_metrics


def reset_metrics():
    """Replace the process-global metrics instance, primarily for tests."""
    global _global_metrics

    with _global_lock:
        _global_metrics = ThreadingMetrics()

    return _global_metrics