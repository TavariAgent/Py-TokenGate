# -*- coding: utf-8 -*-
# core_affinity_queue.py
"""
Core-affinity policy and reporting for token-managed routing.

This module defines task-weight categories, per-weight core preference rules,
and lightweight affinity metrics used to describe how work is distributed
across physical cores.

It does not own mailbox routing or execution. Actual token placement is
performed by the pinned worker queue layer.
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum
from queue import Queue
from typing import List


class TaskWeight(Enum):
    """Routing weight classes used by the affinity policy."""
    HEAVY = "heavy"  # High difficulty work gets Core 1
    MEDIUM = "medium"  # Balanced work gets Core 2+
    LIGHT = "light"  # Simple work gets Core 3+


@dataclass
class CorePreference:
    """Allowed-core set and preferred starting core for one weight class."""
    allowed_cores: List[int]  # Cores this weight can use
    preferred_core: int  # First choice


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

    def get_preference_chain(self, weight: TaskWeight) -> List[int]:
        """Return allowed cores for the given weight in preference order."""
        return self.preferences[weight].allowed_cores

    def can_use_core(self, weight: TaskWeight, core_id: int) -> bool:
        """Return whether the given core is eligible for the given weight."""
        return core_id in self.preferences[weight].allowed_cores


class CoreQueue:
    """Per-core queue and affinity counter container.

    This class stores queue, slot, and worker-pattern state for a single core.
    It is separate from the policy-only affinity manager and may be used by
    execution-layer components that need concrete per-core queue objects.
    """
    def __init__(self, core_id: int, workers_per_core: int = 4):
        self.core_id = core_id
        self.workers_per_core = workers_per_core

        # Queue for tokens
        self.queue = Queue()

        # Slot management
        self.semaphore = threading.Semaphore(workers_per_core)

        # Affinity tracking
        self.heavy_count = 0
        self.medium_count = 0
        self.light_count = 0
        self._affinity_lock = threading.Lock()

        # Dynamic workers (will be created later)
        self.workers = []
        self.current_pattern = workers_per_core  # Default: all workers active

    def has_capacity(self) -> bool:
        """Return whether the core currently has at least one free worker slot."""
        return self.semaphore._value > 0

    def record_task(self, weight: TaskWeight):
        """Increment the observed usage counter for the given weight."""
        with self._affinity_lock:
            if weight == TaskWeight.HEAVY:
                self.heavy_count += 1
            elif weight == TaskWeight.MEDIUM:
                self.medium_count += 1
            else:
                self.light_count += 1

    def get_affinity_stats(self) -> dict:
        """Return percentage and total task distribution for this core."""
        with self._affinity_lock:
            total = self.heavy_count + self.medium_count + self.light_count
            if total == 0:
                return {'heavy': 0.0, 'medium': 0.0, 'light': 0.0}

            return {
                'heavy': (self.heavy_count / total) * 100,
                'medium': (self.medium_count / total) * 100,
                'light': (self.light_count / total) * 100,
                'total_tasks': total
            }

    def set_pattern(self, pattern: int):
        """Update the number of active workers associated with this core."""
        if pattern == self.current_pattern:
            return

        self.current_pattern = pattern

        # Dynamic workers handle this via set_active()
        if self.workers:
            for i, worker in enumerate(self.workers):
                should_be_active = (i < pattern)
                if hasattr(worker, 'set_active'):
                    worker.set_active(should_be_active)


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

    def get_valid_cores_for_weight(self, weight: TaskWeight) -> List[int]:
        """Return the allowed core chain for the given weight."""
        return self.policy.get_preference_chain(weight)

    def record_task_routed(self, core_id: int, weight: TaskWeight):
        """Record one completed routing decision reported by the queue layer."""
        with self._routing_lock:
            self.total_routed += 1
            self._affinity_counts[core_id][weight.value] += 1

    def get_affinity_report(self) -> dict:
        """Return per-core weight distribution percentages and totals."""
        report = {}

        for core_id, counts in self._affinity_counts.items():
            total = sum(counts.values())

            if total > 0:
                # Calculate percentages
                report[f'core_{core_id}'] = {
                    'heavy': (counts['heavy'] / total) * 100,
                    'medium': (counts['medium'] / total) * 100,
                    'light': (counts['light'] / total) * 100,
                    'total_tasks': total
                }
            else:
                # No tasks yet
                report[f'core_{core_id}'] = {
                    'heavy': 0.0,
                    'medium': 0.0,
                    'light': 0.0,
                    'total_tasks': 0
                }

        return report

    def get_stats(self) -> dict:
        """Return a composite snapshot of affinity configuration and routing totals."""
        affinity_report = self.get_affinity_report()

        return {
            'num_cores': self.num_cores,
            'workers_per_core': self.workers_per_core,
            'total_routed': self.total_routed,
            'routing_failures': self.routing_failures,
            'affinity_distribution': affinity_report
        }

    def print_affinity_report(self):
        """Print a human-readable per-core affinity distribution report."""
        print()
        print("=" * 70)
        print("CORE AFFINITY REPORT")
        print("=" * 70)

        # Get report dict
        report = self.get_affinity_report()

        # Print each core's stats
        for core_id in range(1, self.num_cores + 1):
            core_key = f'core_{core_id}'

            if core_key in report:
                stats = report[core_key]

                print(f"\nCore {core_id}:")
                print(f"  Heavy:  {stats['heavy']:>5.1f}%")
                print(f"  Medium: {stats['medium']:>5.1f}%")
                print(f"  Light:  {stats['light']:>5.1f}%")
                print(f"  Total:  {stats['total_tasks']} tasks")

        print("=" * 70)


# ============================================================================
# TESTING
# ============================================================================

if __name__ == '__main__':
    from .topology_detector import TopologyDetector
    from .token_system import TaskToken, TokenMetadata

    print("=" * 70)
    print("CORE AFFINITY QUEUE TEST")
    print("=" * 70)
    print()

    # Detect topology
    detector = TopologyDetector()
    topology = detector.detect()

    print(f"System: {topology.physical_cores} physical cores")
    print()

    # Create affinity queue
    affinity_queue = CoreAffinityQueue(topology, workers_per_core=4)

    print()
    print("-" * 70)
    print("TEST: Route tokens to cores")
    print("-" * 70)
    print()


    # Create test tokens with different weights
    def dummy_func():
        return "test"


    tokens = []

    # Heavy tasks (should go to Core 1)
    for i in range(3):
        meta = TokenMetadata(
            operation_type='compute_heavy',
            created_at=time.time(),
            tags={'weight': 'heavy'}
        )
        token = TaskToken(f'heavy_{i}', dummy_func, (), {}, meta)
        tokens.append(('heavy', token))

    # Medium tasks (should go to Core 2+)
    for i in range(3):
        meta = TokenMetadata(
            operation_type='process_medium',
            created_at=time.time(),
            tags={'weight': 'medium'}
        )
        token = TaskToken(f'medium_{i}', dummy_func, (), {}, meta)
        tokens.append(('medium', token))

    # Light tasks (should go to Core 3+)
    for i in range(3):
        meta = TokenMetadata(
            operation_type='io_light',
            created_at=time.time(),
            tags={'weight': 'light'}
        )
        token = TaskToken(f'light_{i}', dummy_func, (), {}, meta)
        tokens.append(('light', token))

    # Print report
    affinity_queue.print_affinity_report()

    print()
    print("Stats:")
    stats = affinity_queue.get_stats()
    print(f"  Total routed: {stats['total_routed']}")
    print(f"  Failures: {stats['routing_failures']}")

    print()
    print("Test complete!")