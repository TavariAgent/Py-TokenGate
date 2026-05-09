# -*- coding: utf-8 -*-
# prometheus_convergence.py
"""
Prometheus-backed convergence policy for worker-pattern adaptation.

This module evaluates per-core pressure using observable runtime metrics
instead of indirect OS-level CPU measurements.

Primary indicators:
1. Queue wait time
2. Worker utilization
3. Queue depth
4. Task duration trend

These signals are used to recommend per-core worker patterns under
overloaded, balanced, or underutilized conditions.
"""

import time
from collections import deque
from typing import Dict, List, Optional
from enum import Enum
from dataclasses import dataclass

from .threading_metrics import get_metrics
from prometheus_client import generate_latest
from .tg_print import tg_print

# Weight numeric scores for heuristic averaging
_WEIGHT_SCORE = {'heavy': 3.0, 'medium': 2.0, 'light': 1.0}

class WorkerPattern(Enum):
    """Per-core worker allocation patterns used by convergence."""
    HEAVY = 2  # 2 workers per core (high contention)
    MEDIUM = 3  # 3 workers per core (balanced, default)
    LIGHT = 4  # 4 workers per core (underutilized)


@dataclass
class CorePressure:
    """Observed pressure summary and recommendation for one core."""
    core_id: int
    queue_depth: int
    worker_utilization: float  # 0-100%
    queue_wait_p95: Optional[float]  # seconds
    avg_task_duration: Optional[float]  # seconds
    pressure_level: str  # 'overloaded', 'balanced', 'underutilized'
    recommended_pattern: WorkerPattern


class PrometheusConvergenceEngine:
    """Convergence engine driven by observable Prometheus metrics.

    Assesses per-core pressure from queue, utilization, and duration signals,
    then recommends worker-pattern adjustments without relying on OS CPU
    percentage measurements.
    """
    def __init__(
            self,
            topology,
            queue_wait_threshold: float = 1.0, # p95 > 1s = overloaded
            utilization_high: float = 75.0, # >75% = saturated
            utilization_low: float = 30.0, # <25% = underutilized
            queue_depth_factor: int = 2, # queue > workers*2 = overloaded
    ):
        """
        Initialize the convergence engine and default per-core patterns.

        Args:
            topology: CPU topology provider exposing physical core count.
            queue_wait_threshold: p95 queue wait above this value signals overload.
            utilization_high: Utilization threshold indicating saturation.
            utilization_low: Utilization threshold indicating underutilization.
            queue_depth_factor: Queue depth multiplier used in overload checks.
        """
        self.topology = topology
        self.queue_wait_threshold = queue_wait_threshold
        self.utilization_high = utilization_high
        self.utilization_low = utilization_low
        self.queue_depth_factor = queue_depth_factor
        self.metrics = get_metrics()

        # Track current patterns
        self.core_patterns: Dict[int, WorkerPattern] = {}
        for core_id in range(1, topology.physical_cores + 1):
            self.core_patterns[core_id] = WorkerPattern.LIGHT

        # Track convergence history
        self.convergence_history: List[Dict] = []

        # Per-core weight heuristic gauge (rolling window per core_id)
        self._weight_window_size = 50
        self._weight_history: Dict[int, deque] = {}
        for core_id in range(1, topology.physical_cores + 1):
            self._weight_history[core_id] = deque(maxlen=self._weight_window_size)

    # ------------------------------------------------------------------
    # Weight heuristic gauge — per-core rolling average
    # ------------------------------------------------------------------

    def record_task_weight(self, core_id: int, weight_name: str):
        """Record an incoming task's weight for heuristic utilization.

        Call this when a task is routed so the convergence engine can
        gauge per-core pressure from the frequency and heaviness of
        recent work rather than from active-worker counts.

        Args:
            core_id: The 1-based physical core the task was routed to.
            weight_name: One of 'heavy', 'medium', or 'light'.
        """
        score = _WEIGHT_SCORE.get(weight_name.lower(), 2.0)
        entry = (time.monotonic(), score)
        if core_id in self._weight_history:
            self._weight_history[core_id].append(entry)

    def gauge_utilization(self, core_id: int, window_seconds: float = 10.0) -> float:
        """Return heuristic utilization (0-100) for *core_id*.

        The value is derived from the average weight score of tasks that
        arrived within the last *window_seconds*.  A stream of heavy
        tasks yields ~100 %; a stream of light tasks yields ~33 %;
        no recent tasks yields 0 %.

        The score is normalised against the maximum weight (heavy = 3.0)
        so the returned percentage is directly comparable with the
        threshold fields ``utilization_high`` and ``utilization_low``.
        """
        history = self._weight_history.get(core_id)
        if not history:
            return 0.0

        now = time.monotonic()
        cutoff = now - window_seconds

        # Collect scores within the time window
        recent_scores = [score for ts, score in history if ts >= cutoff]
        if not recent_scores:
            return 0.0

        avg_score = sum(recent_scores) / len(recent_scores)
        max_score = _WEIGHT_SCORE['heavy']  # 3.0

        # Scale by frequency: more tasks in the window = higher utilization
        # Normalise task count against the window capacity expectation
        # (window_size tasks in window_seconds = fully saturated)
        frequency_factor = min(1.0, len(recent_scores) / self._weight_window_size)

        utilization = (avg_score / max_score) * frequency_factor * 100.0
        return round(utilization, 2)

    def get_weight_summary(self, core_id: int, window_seconds: float = 10.0) -> dict:
        """Return a diagnostic summary of recent weight observations."""
        history = self._weight_history.get(core_id)
        if not history:
            return {'recent_tasks': 0, 'avg_weight': 0.0, 'heuristic_util': 0.0}

        now = time.monotonic()
        cutoff = now - window_seconds
        recent = [(ts, s) for ts, s in history if ts >= cutoff]

        if not recent:
            return {'recent_tasks': 0, 'avg_weight': 0.0, 'heuristic_util': 0.0}

        avg = sum(s for _, s in recent) / len(recent)
        return {
            'recent_tasks': len(recent),
            'avg_weight': round(avg, 2),
            'heuristic_util': self.gauge_utilization(core_id, window_seconds),
        }

    def analyze_cores(self, worker_pool) -> List[CorePressure]:
        """Analyze all physical cores using current Prometheus metric output."""
        pressures = []

        # Get current Prometheus data
        registry = self.metrics.get_registry()
        prom_data = generate_latest(registry).decode('utf-8')

        # Parse metrics for each core
        for core_id in range(1, self.topology.physical_cores + 1):
            pressure = self._analyze_single_core(core_id, prom_data, worker_pool)
            pressures.append(pressure)

        return pressures

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

        # Heuristic utilization from weight gauge (replaces active-worker %)
        utilization = self.gauge_utilization(core_id)

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
            queue_depth=int(queue_depth) if queue_depth is not None else 0,
            worker_utilization=utilization if utilization is not None else 0.0,
            queue_wait_p95=queue_wait_p95,
            avg_task_duration=avg_duration,
            pressure_level=pressure_level,
            recommended_pattern=recommended
        )

    def _assess_pressure(
            self,
            core_id: int,
            queue_depth: Optional[float],
            utilization: Optional[float],
            queue_wait_p95: Optional[float],
            worker_pool
    ) -> tuple[str, WorkerPattern]:
        """Classify core pressure and return the recommended worker pattern."""
        workers_per_core = worker_pool.workers_per_core if worker_pool else 4

        # Convert None to 0 for comparisons
        queue_depth = queue_depth if queue_depth is not None else 0.0
        utilization = utilization if utilization is not None else 0.0
        queue_wait_p95 = queue_wait_p95 if queue_wait_p95 is not None else 0.0

        # RULE 1: High queue wait time = OVERLOADED
        if queue_wait_p95 > self.queue_wait_threshold:
            tg_print('convergence', f'Core {core_id}: queue wait p95={queue_wait_p95:.2f}s > threshold {self.queue_wait_threshold}s  -> overloaded')
            return 'overloaded', WorkerPattern.HEAVY

        # RULE 2: Deep queue = OVERLOADED
        if queue_depth > workers_per_core * self.queue_depth_factor:
            tg_print('convergence', f'Core {core_id}: queue depth={queue_depth} > {workers_per_core * self.queue_depth_factor}  -> overloaded')
            return 'overloaded', WorkerPattern.HEAVY

        # RULE 3: High utilization + any queue = OVERLOADED
        if utilization > self.utilization_high and queue_depth > 0:
            tg_print('convergence', f'Core {core_id}: utilization={utilization:.1f}% > {self.utilization_high}% with queue  -> overloaded')
            return 'overloaded', WorkerPattern.HEAVY

        # RULE 4: Low utilization = UNDERUTILIZED
        if utilization < self.utilization_low:
            tg_print('convergence', f'Core {core_id}: utilization={utilization:.1f}% < {self.utilization_low}%  -> underutilized')
            return 'underutilized', WorkerPattern.LIGHT

        # RULE 5: Everything else = BALANCED
        return 'balanced', WorkerPattern.MEDIUM

    def recommend_adjustments(
            self,
            core_pressures: List[CorePressure]
    ) -> Dict[int, WorkerPattern]:
        """Return only the per-core pattern changes that differ from current state."""
        adjustments = {}

        for pressure in core_pressures:
            current = self.core_patterns[pressure.core_id]
            recommended = pressure.recommended_pattern

            if current != recommended:
                adjustments[pressure.core_id] = recommended

                # Log the reason for the change and current pressure signals
                tg_print('convergence',f'Core {pressure.core_id}: {current.name} -> {recommended.name}  reason={pressure.pressure_level}')
                tg_print('convergence', f'depth={pressure.queue_depth}  util={pressure.worker_utilization:.1f}%' + (
                             f'wait_p95={pressure.queue_wait_p95:.2f}s' if pressure.queue_wait_p95 else ''), level='debug')

        return adjustments

    def apply_pattern(
            self,
            core_id: int,
            pattern: WorkerPattern,
            worker_pool=None
    ):
        """Apply a recommended pattern, record it in metrics, and log history.

        If the provided worker pool supports dynamic per-core patterns, the
        change is applied to the live pool as well.
        """
        old_pattern = self.core_patterns[core_id]
        self.core_patterns[core_id] = pattern

        # Record in Prometheus
        self.metrics.update_pattern(core_id, pattern.value)

        if old_pattern != pattern:
            if worker_pool and hasattr(worker_pool, 'set_pattern'):
                try:
                    worker_pool.set_pattern(core_id, pattern.value)
                    tg_print('convergence', f'Core {core_id}: pattern applied to worker pool', level='dispatch')
                except Exception as e:
                    tg_print('convergence', f'Core {core_id}: could not apply pattern to pool: {e}', level='warn')

            self.metrics.record_convergence_change(
                core_id,
                old_pattern.value,
                pattern.value
            )

            self.convergence_history.append({
                'timestamp': time.time(),
                'core_id': core_id,
                'from_pattern': old_pattern.name,
                'to_pattern': pattern.name
            })
            tg_print('convergence', f'Core {core_id}: {old_pattern.name} ({old_pattern.value} workers) '
                                        f'-> {pattern.name} ({pattern.value} workers)', level='state')

    def get_convergence_status(self) -> dict:
        """Get a current convergence state."""
        distribution = {
            'heavy': sum(1 for p in self.core_patterns.values() if p == WorkerPattern.HEAVY),
            'medium': sum(1 for p in self.core_patterns.values() if p == WorkerPattern.MEDIUM),
            'light': sum(1 for p in self.core_patterns.values() if p == WorkerPattern.LIGHT)
        }

        return {
            'core_patterns': {
                core_id: pattern.name
                for core_id, pattern in self.core_patterns.items()
            },
            'pattern_distribution': distribution,
            'total_changes': len(self.convergence_history),
            'recent_changes': self.convergence_history[-5:] if self.convergence_history else [],
            'weight_gauge': {
                core_id: self.get_weight_summary(core_id)
                for core_id in self.core_patterns
            }
        }

    # Utility methods for parsing Prometheus data
    @staticmethod
    def _extract_gauge(
            prom_data: str,
            metric_name: str,
            labels: Dict[str, str]
    ) -> Optional[float]:
        """Extract gauge value from Prometheus data."""
        label_str = ','.join(f'{k}="{v}"' for k, v in labels.items())
        search = f'{metric_name}{{{label_str}}}'

        for line in prom_data.split('\n'):
            if search in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return float(parts[-1])
                    except ValueError:
                        pass
        return None

    @staticmethod
    def _calculate_histogram_percentile(
            prom_data: str,
            metric_name: str,
            labels: Dict[str, str],
            percentile: float
    ) -> Optional[float]:
        """
        Calculate percentile from histogram buckets.

        Simplified calculation - would use proper quantile estimation.
        For now, returns the bucket le value where count >= target.
        """
        # Build label string
        base_labels = ','.join(f'{k}="{v}"' for k, v in labels.items())

        # Parse buckets
        buckets = []
        for line in prom_data.split('\n'):
            if f'{metric_name}_bucket' in line and base_labels in line:
                # Extract le value and count
                if 'le=' in line:
                    try:
                        le_val = line.split('le="')[1].split('"')[0]
                        if le_val == '+Inf':
                            continue
                        count = float(line.split()[-1])
                        buckets.append((float(le_val), count))
                    except:
                        continue

        if not buckets:
            return None

        # Sort by le value
        buckets.sort(key=lambda x: x[0])

        # Find total count
        total = buckets[-1][1] if buckets else 0
        if total == 0:
            return None

        # Find bucket containing percentile
        target = total * percentile
        for le, count in buckets:
            if count >= target:
                return le

        return buckets[-1][0] if buckets else None

    @staticmethod
    def _calculate_histogram_average(
            prom_data: str,
            metric_name: str,
            labels: Dict[str, str]
    ) -> Optional[float]:
        """Calculate average from histogram sum and count."""
        label_str = ','.join(f'{k}="{v}"' for k, v in labels.items())

        sum_val = None
        count_val = None

        for line in prom_data.split('\n'):
            if f'{metric_name}_sum{{{label_str}}}' in line:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        sum_val = float(parts[-1])
                    except ValueError:
                        pass

            elif f'{metric_name}_count{{{label_str}}}' in line:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        count_val = float(parts[-1])
                    except ValueError:
                        pass

        if sum_val is not None and count_val and count_val > 0:
            return sum_val / count_val

        return None