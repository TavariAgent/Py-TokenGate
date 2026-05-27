# -*- coding: utf-8 -*-
# allocation_optimizer.py
"""
Confidence-bound allocation optimization for token-managed operations.

This module tracks per-operation memory allocation state and applies
bounded allocation reductions only when confidence remains above a
moving safety boundary.

The optimizer supports progressive reduction, hold behavior when
confidence stalls, and reversion after failed optimization attempts.
"""

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

from .code_inspector import CodeMetrics, ComplexityLevel
from .tg_print import tg_print


class OptimizationDecision(Enum):
    """Decision outcomes for allocation adjustment attempts."""
    OPTIMIZE = "optimize"  # Go ahead with reduction
    HOLD = "hold"  # Keep current allocation
    REVERT = "revert"  # Failed - go back
    BLOCKED = "blocked"  # Below confidence boundary


@dataclass
class OperationAllocation:
    """Mutable allocation and confidence state for one operation type."""
    operation_name: str
    complexity_level: ComplexityLevel

    # Allocations
    current_allocation_mb: int
    baseline_allocation_mb: int

    # Confidence tracking
    baseline_confidence: float
    confidence_boundary: float
    last_observed_confidence: float

    # Execution history
    successful_executions: int = 0
    failed_executions: int = 0
    successful_reductions: int = 0
    total_reductions_attempted: int = 0

    # State
    last_optimization_time: float = 0.0
    currently_optimizing: bool = False

    def get_optimization_rate(self) -> float:
        """Return the configured reduction rate for this complexity level."""
        rates = {
            ComplexityLevel.TRIVIAL: 0.05,  # 5% - stable
            ComplexityLevel.SIMPLE: 0.03,  # 3% - growth
            ComplexityLevel.MODERATE: 0.02,  # 2% - standard
            ComplexityLevel.COMPLEX: 0.015,  # 1.5% - volatile
            ComplexityLevel.EXTREME: 0.01  # 1% - scalping
        }
        return rates[self.complexity_level]

    def get_success_rate(self) -> float:
        """Return the execution success percentage for this operation."""
        total = self.successful_executions + self.failed_executions
        if total == 0:
            return 0.0
        return (self.successful_executions / total) * 100

    def to_dict(self) -> Dict:
        """Return a serialization-friendly snapshot of allocation state."""
        return {
            'operation_name': self.operation_name,
            'complexity_level': self.complexity_level.name,
            'current_allocation_mb': self.current_allocation_mb,
            'baseline_allocation_mb': self.baseline_allocation_mb,
            'baseline_confidence': self.baseline_confidence,
            'confidence_boundary': self.confidence_boundary,
            'last_observed_confidence': self.last_observed_confidence,
            'successful_executions': self.successful_executions,
            'failed_executions': self.failed_executions,
            'successful_reductions': self.successful_reductions,
            'total_reductions_attempted': self.total_reductions_attempted,
            'success_rate': self.get_success_rate(),
            'optimization_rate': self.get_optimization_rate() * 100
        }


class AllocationOptimizer:
    """Manages confidence-gated allocation adjustment across operations.

    Tracks per-operation baselines, current allocation, confidence
    boundaries, and optimization outcome history in order to decide
    when reductions are allowed, blocked, or reverted.
    """

    # Confidence boundary constraints
    MIN_BOUNDARY = 75.0  # Never require less than 75% confidence
    MAX_BOUNDARY = 95.0  # Cap boundary growth at 95%
    BOUNDARY_INCREMENT = 2.0  # Raise the boundary by 2% per successful reduction

    # High-confidence lock threshold
    # (This effectively blocks the optimization from reoccurring too frequently or exceeding safe values)
    HIGH_CONFIDENCE_LOCK = 85.0  # At 85%+, cap boundary at 90%

    def __init__(self, base_budget_mb: int = 50):
        """Initialize the optimizer with a baseline memory budget."""
        self.base_budget_mb = base_budget_mb

        # Track allocations per operation
        self.allocations: Dict[str, OperationAllocation] = {}
        self._lock = threading.Lock()

        # Statistics
        self.total_optimizations_attempted = 0
        self.total_optimizations_successful = 0
        self.total_optimizations_reverted = 0

    def initialize_operation(
            self,
            operation_name: str,
            metrics: CodeMetrics
    ) -> OperationAllocation:
        """
        Create baseline allocation state for a previously unseen operation.

        Uses CodeInspector-derived metrics to establish initial allocation,
        baseline confidence, and the starting confidence boundary.
        """
        with self._lock:
            if operation_name in self.allocations:
                return self.allocations[operation_name]

            # Get initial allocation from the code inspector
            from .code_inspector import CodeInspector
            initial = CodeInspector.predict_initial_allocation(metrics, self.base_budget_mb)

            # Create allocation object metrics
            allocation = OperationAllocation(
                operation_name=operation_name,
                complexity_level=metrics.complexity_level,
                current_allocation_mb=initial['memory_mb'],
                baseline_allocation_mb=initial['memory_mb'],
                baseline_confidence=metrics.confidence,
                confidence_boundary=metrics.confidence,
                last_observed_confidence=metrics.confidence
            )

            self.allocations[operation_name] = allocation

            tg_print('overflow', f'Optimizer initialized: {operation_name}', level='debug')
            tg_print('overflow', f'Complexity: {metrics.complexity_level.name}  '
                                 f'baseline={allocation.baseline_allocation_mb} MB  '
                                 f'confidence={allocation.baseline_confidence}%', level='debug')

            return allocation

    def can_optimize(
            self,
            operation_name: str,
            current_confidence: float
    ) -> Tuple[OptimizationDecision, str]:
        """
        Decide whether allocation reduction is currently permitted.

        Returns the decision and a human-readable reason describing the
        boundary, minimum-allocation, or confidence-delta outcome.
        """
        with self._lock:
            if operation_name not in self.allocations:
                return OptimizationDecision.BLOCKED, "Operation not initialized"

            allocation = self.allocations[operation_name]

            # Check if already at baseline (can't reduce further)
            if allocation.current_allocation_mb <= allocation.baseline_allocation_mb * 0.5:
                return OptimizationDecision.HOLD, "Already at minimum (50% of baseline)"

            # Check confidence boundary
            if current_confidence < allocation.confidence_boundary:
                gap = allocation.confidence_boundary - current_confidence
                return (
                    OptimizationDecision.BLOCKED,
                    f"Below boundary: {current_confidence}% < {allocation.confidence_boundary}% (gap: {gap:.1f}%)"
                )

            # Check if confidence is gaining
            confidence_delta = current_confidence - allocation.last_observed_confidence

            if allocation.successful_reductions > 0 >= confidence_delta:
                # We have already tightened successfully before,
                # but confidence is no longer improving.
                # Hold instead of reducing further.
                # Confidence not gaining after we've already optimized
                return (
                    OptimizationDecision.HOLD,
                    f"Confidence not gaining (delta: {confidence_delta:.1f}%), holding at current allocation"
                )

            # All checks passed!
            return (
                OptimizationDecision.OPTIMIZE,
                f"Confidence {current_confidence}% >= boundary {allocation.confidence_boundary}%,"
                f" delta: +{confidence_delta:.1f}%"
            )

    def attempt_optimization(
            self,
            operation_name: str,
            current_confidence: float
    ) -> Optional[int]:
        """
        Attempt one allocation reduction for the operation.

        Returns the proposed new allocation in MB if optimization is allowed,
        otherwise returns None.
        """
        decision, reasoning = self.can_optimize(operation_name, current_confidence)

        tg_print('overflow',
                 f'Optimizer {operation_name}: {decision.value}  — {reasoning}', level='debug')

        if decision != OptimizationDecision.OPTIMIZE:
            return None

        with self._lock:
            allocation = self.allocations[operation_name]

            # Calculate reduction
            rate = allocation.get_optimization_rate()
            new_allocation = int(allocation.current_allocation_mb * (1 - rate))

            # Don't go below baseline minimum
            min_allocation = int(allocation.baseline_allocation_mb * 0.5)
            new_allocation = max(new_allocation, min_allocation)

            tg_print('overflow', f'Reducing: {allocation.current_allocation_mb} '
                                 f'MB -> {new_allocation} MB  ({rate * 100}% reduction)', level='debug')

            # Mark as optimizing
            allocation.currently_optimizing = True
            allocation.total_reductions_attempted += 1
            allocation.last_optimization_time = time.time()

            self.total_optimizations_attempted += 1

            return new_allocation

    def record_execution_result(
            self,
            operation_name: str,
            success: bool,
            new_confidence: float,
    ):
        """
        Record execution outcome and update confidence-boundary state.

        Successful optimization with confidence gain raises the boundary.
        Successful optimization without gain holds current state.
        Failed optimization reverts allocation and resets the boundary.
        """
        with self._lock:
            if operation_name not in self.allocations:
                return

            allocation = self.allocations[operation_name]

            # Update execution counts
            if success:
                allocation.successful_executions += 1
            else:
                allocation.failed_executions += 1

            # Check if this was during optimization
            if not allocation.currently_optimizing:
                # Normal execution - just update confidence
                allocation.last_observed_confidence = new_confidence
                return

            # Attempted optimization flag
            allocation.currently_optimizing = False

            confidence_delta = new_confidence - allocation.last_observed_confidence

            tg_print('overflow', f'Optimizer result: {operation_name}  '
                                 f'success={success}  '
                                 f'confidence {allocation.last_observed_confidence}% -> {new_confidence}%  '
                                 f'delta={confidence_delta:+.1f}%', level='debug')

            if success and confidence_delta > 0:
                # SUCCESS + CONFIDENCE GAIN -> RATCHET UP!
                allocation.successful_reductions += 1
                self.total_optimizations_successful += 1

                # Raise the boundary
                new_boundary = allocation.confidence_boundary + self.BOUNDARY_INCREMENT

                # Apply caps
                if allocation.last_observed_confidence >= self.HIGH_CONFIDENCE_LOCK:
                    # High confidence - cap at 90%
                    new_boundary = min(new_boundary, 90.0)
                else:
                    # Normal cap at 95%
                    new_boundary = min(new_boundary, self.MAX_BOUNDARY)

                tg_print('overflow',
                         f'Ratchet: '
                         f'boundary {allocation.confidence_boundary}% -> {new_boundary}%', level='debug')

                allocation.confidence_boundary = new_boundary
                allocation.last_observed_confidence = new_confidence

            elif success and confidence_delta <= 0:
                # SUCCESS but NO GAIN -> HOLD
                tg_print('overflow',
                         f'Hold: {operation_name} succeeded but confidence did not gain', level='debug')
                allocation.last_observed_confidence = new_confidence

            else:
                # FAILURE -> REVERT
                tg_print('overflow',
                         f'Revert: {operation_name} failed — restoring allocation', level='warn')

                # Revert to previous allocation
                rate = allocation.get_optimization_rate()
                previous_allocation = int(allocation.current_allocation_mb / (1 - rate))
                allocation.current_allocation_mb = previous_allocation

                # Reset boundary to baseline
                allocation.confidence_boundary = allocation.baseline_confidence

                self.total_optimizations_reverted += 1

                tg_print('overflow', f'Reverted to {allocation.current_allocation_mb} '
                                     f'MB  boundary reset to {allocation.confidence_boundary}%', level='warn')

    def get_allocation(self, operation_name: str) -> Optional[int]:
        """Return the current allocation for the operation, if tracked."""
        with self._lock:
            if operation_name in self.allocations:
                return self.allocations[operation_name].current_allocation_mb
            return None

    def get_operation_stats(self, operation_name: str) -> Optional[Dict]:
        """Return detailed allocation and execution statistics for one operation."""
        with self._lock:
            if operation_name in self.allocations:
                return self.allocations[operation_name].to_dict()
            return None

    def get_all_stats(self) -> Dict:
        """Return aggregate optimizer statistics and per-operation snapshots."""
        with self._lock:
            return {
                'total_operations': len(self.allocations),
                'total_optimizations_attempted': self.total_optimizations_attempted,
                'total_optimizations_successful': self.total_optimizations_successful,
                'total_optimizations_reverted': self.total_optimizations_reverted,
                'success_rate': (
                    (self.total_optimizations_successful / self.total_optimizations_attempted * 100)
                    if self.total_optimizations_attempted > 0 else 0.0
                ),
                'operations': {
                    name: alloc.to_dict()
                    for name, alloc in self.allocations.items()
                }
            }

    # TODO: Add too guard house dashboard
    def print_portfolio(self):
        """Print a human-readable summary of all tracked allocation state."""
        with self._lock:
            print()
            print("=" * 80)
            print("ALLOCATION OPTIMIZER PORTFOLIO")
            print("=" * 80)
            print()

            print(f"Total Operations: {len(self.allocations)}")
            print(f"Optimizations Attempted: {self.total_optimizations_attempted}")
            print(f"Optimizations Successful: {self.total_optimizations_successful}")
            print(f"Optimizations Reverted: {self.total_optimizations_reverted}")

            if self.total_optimizations_attempted > 0:
                success_rate = (self.total_optimizations_successful / self.total_optimizations_attempted) * 100
                print(f"Success Rate: {success_rate:.1f}%")

            print()
            print("-" * 80)
            print("OPERATION DETAILS")
            print("-" * 80)

            for name, alloc in sorted(self.allocations.items()):
                print()
                print(f"{name}:")
                print(f"  Complexity: {alloc.complexity_level.name} (rate: {alloc.get_optimization_rate() * 100}%)")
                print(f"  Allocation: {alloc.current_allocation_mb} MB (baseline: {alloc.baseline_allocation_mb} MB)")
                print(f"  Confidence: {alloc.last_observed_confidence:.1f}%")
                print(f"  Boundary: {alloc.confidence_boundary:.1f}%")
                print(f"  Executions: {alloc.successful_executions} success, {alloc.failed_executions} failed")
                print(f"  Reductions: {alloc.successful_reductions}/{alloc.total_reductions_attempted}")

                if alloc.successful_executions + alloc.failed_executions > 0:
                    print(f"  Success Rate: {alloc.get_success_rate():.1f}%")

            print()
            print("=" * 80)
