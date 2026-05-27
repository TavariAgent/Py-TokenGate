# -*- coding: utf-8 -*-
# overflow_guard.py
"""
Overflow guard for pre-execution allocation hints and bounded retry recovery.

Combines code inspection, confidence-based allocation state, retry eligibility
checks, and backup-token tracking in one component.

Retries are recreated with bumped allocations and re-injected into the global
token pool under bounded retry policies derived from complexity level.
"""

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Callable, Any

# Add the directory containing this file to the Python path
# This makes imports work regardless of where the project is cloned
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from .token_system import global_token_pool, TaskToken, TokenMetadata
from .code_inspector import CodeInspector, ComplexityLevel
from .allocation_optimizer import AllocationOptimizer
from .tg_print import tg_print


@dataclass
class RetryPolicy:
    """Retry limits and allocation bump rate for one complexity level."""
    max_retries: int
    allocation_bump_percent: float  # Decimal (0.05 = 5%)

    @staticmethod
    def for_complexity(level: ComplexityLevel) -> 'RetryPolicy':
        """Get retry policy for complexity level."""
        policies = {
            ComplexityLevel.TRIVIAL: RetryPolicy(max_retries=5, allocation_bump_percent=0.05),
            ComplexityLevel.SIMPLE: RetryPolicy(max_retries=4, allocation_bump_percent=0.07),
            ComplexityLevel.MODERATE: RetryPolicy(max_retries=3, allocation_bump_percent=0.10),
            ComplexityLevel.COMPLEX: RetryPolicy(max_retries=2, allocation_bump_percent=0.12),
            ComplexityLevel.EXTREME: RetryPolicy(max_retries=2, allocation_bump_percent=0.15)
        }
        return policies[level]


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

    def calculate_next_allocation(self) -> int:
        """Calculate allocation for next retry."""
        return int(self.current_allocation_mb * (1 + self.bump_percent))


class OverflowGuard:
    """Pre-execution allocation guard and bounded retry manager.

    Inspects tokens before execution, derives initial allocation guidance,
    tracks retry state for failed tokens, and recreates retry tokens when
    retry policy and failure conditions permit.
    """
    # Failure detection thresholds (seconds)
    MIN_FAILURE_DURATION = 10.0  # < 10s = quick failure (likely error)
    MAX_FAILURE_DURATION = 60.0  # > 60s = timeout/stall

    def __init__(self, base_budget_mb: int = 50):
        """
        Initialize overflow guard.

        Args:
            base_budget_mb: Base memory budget for operations
        """
        self.base_budget_mb = base_budget_mb

        # Inspection & optimization
        self.optimizer = AllocationOptimizer(base_budget_mb)

        # Backup token pool
        self.BACKUP_TOKENS: Dict[str, BackupToken] = {}
        self._backup_lock = threading.Lock()

        # Metrics
        self.total_inspections = 0
        self.total_retries_created = 0
        self.total_retries_succeeded = 0
        self.total_retries_exhausted = 0

        tg_print('overflow', 'Initialized')
        tg_print('overflow', f'Base budget: {base_budget_mb} MB')
        tg_print('overflow', 'Retry policies loaded for all complexity levels')

    def inspect_token(self, token: TaskToken) -> Dict[str, Any]:
        """
        Inspect a token's function and predict resource needs.

        This is called when a token is created to set initial allocations.

        Args:
            token: The token to inspect

        Returns:
            Dict containing metrics, recommended allocation, timeout, confidence,
            and optionally an error string when inspection fails.
        """
        self.total_inspections += 1

        # Extract function from token
        func = token.func

        # Analyze bytecode
        try:
            metrics = CodeInspector.analyze(func)
        except Exception as e:
            tg_print('overflow', f'Failed to inspect {token.token_id}: {e}', level='error')
            # Return safe defaults
            return {
                'metrics': None,
                'allocation_mb': self.base_budget_mb,
                'timeout_seconds': 30.0,
                'confidence': 50.0,
                'error': str(e)
            }

        # Initialize operation in optimizer
        operation_name = token.metadata.operation_type or "unnamed"
        self.optimizer.initialize_operation(operation_name, metrics)

        # Get allocation recommendation
        allocation = CodeInspector.predict_initial_allocation(metrics, self.base_budget_mb)

        tg_print('overflow', f'Inspected {token.token_id}', level='debug')
        tg_print('overflow', f'Operation: {operation_name}', level='debug')
        tg_print('overflow', f'Complexity: {metrics.complexity_level.name} '
                             f'(score: {metrics.complexity_score:.1f})', level='debug')
        tg_print('overflow', f'Allocation: {allocation["memory_mb"]} MB', level='debug')
        tg_print('overflow', f'Confidence: {allocation["confidence"]}%', level='debug')

        return {
            'metrics': metrics,
            'allocation_mb': allocation['memory_mb'],
            'timeout_seconds': allocation['timeout_seconds'],
            'confidence': allocation['confidence']
        }

    def should_retry(
            self, token_id: str,
            execution_duration: float,
            success: bool,
            operation_type: Optional[str] = None,
            token_tags: Optional[dict] = None
    ) -> bool:
        """
        Determine if a task should be retried.

        Retry criteria:
        - Execution took 10-60 seconds (failure zone)
        - Task did not succeed
        - We haven't exhausted retries
        - NOT a FINALE task (designed to fail/hang)

        Args:
            token_id: Token that executed
            execution_duration: How long it took (seconds)
            success: Did it succeed?
            operation_type: Operation type (optional, for exclusion checks)
            token_tags: Token tags (optional, for exclusion checks)

        Returns:
            True if should retry
        """
        # Success = no retry needed
        if success:
            return False

        # EXCLUDE I/O OPERATIONS (flagged with allow_retries=false)
        # File writes, database ops, network requests should NOT retry
        # because the same args → same target → corruption/conflicts
        if token_tags and token_tags.get('allow_retries') == 'false':
            tg_print('overflow',
                     f'Skipping retry for I/O operation: {operation_type}', level='warn')
            tg_print('overflow',
                     'Reason: I/O operations with same args risk data corruption', level='warn')
            return False  # ← BLOCK RETRY

        # Check the duration threshold
        in_failure_zone = (
                self.MIN_FAILURE_DURATION <= execution_duration <= self.MAX_FAILURE_DURATION
        )

        if not in_failure_zone:
            # Too fast (error) or too slow (timeout) - don't retry
            return False

        # Check if we have retries left
        with self._backup_lock:
            if token_id in self.BACKUP_TOKENS:
                backup = self.BACKUP_TOKENS[token_id]
                return backup.can_retry()

        # First failure - can create backup
        return True

    def create_retry_token(self, original_token: TaskToken, execution_duration: float) -> Optional[TaskToken]:
        """Create a retry token with bumped allocation under the active retry policy."""
        with self._backup_lock:
            token_id = original_token.token_id

            # Check if this is a retry or first failure
            if token_id in self.BACKUP_TOKENS:
                # This is a retry that failed
                backup = self.BACKUP_TOKENS[token_id]

                if not backup.can_retry():
                    tg_print('overflow', f'Retries exhausted for {token_id}', level='warn')
                    self.total_retries_exhausted += 1
                    return None

                # Bump allocation for next retry
                new_allocation = backup.calculate_next_allocation()
                backup.current_allocation_mb = new_allocation
                backup.current_retry_count += 1
                backup.last_retry_at = time.time()

                tg_print('overflow',
                         f'Retry {backup.current_retry_count}/{backup.max_retries} for {token_id}')
                tg_print('overflow',
                         f'Bumping allocation: {new_allocation} MB (+{backup.bump_percent * 100}%)')

            else:
                # First failure - create backup entry
                # Get complexity from optimizer (or inspect again)
                operation_type: str = original_token.metadata.operation_type or "unnamed"

                # Try to get metrics from optimizer
                op_stats = self.optimizer.get_operation_stats(operation_type)
                if op_stats:
                    complexity_level = ComplexityLevel[op_stats['complexity_level']]
                else:
                    # Default to MODERATE if unknown
                    complexity_level = ComplexityLevel.MODERATE

                # Get retry policy
                policy = RetryPolicy.for_complexity(complexity_level)

                # Get current allocation (from optimizer or default)
                current_alloc = self.optimizer.get_allocation(operation_type)
                if not current_alloc:
                    current_alloc = self.base_budget_mb

                # Create backup tracking
                backup = BackupToken(
                    original_token_id=token_id,
                    current_retry_count=1,
                    max_retries=policy.max_retries,
                    base_allocation_mb=current_alloc,
                    current_allocation_mb=int(current_alloc * (1 + policy.allocation_bump_percent)),
                    bump_percent=policy.allocation_bump_percent,
                    complexity_level=complexity_level,
                    created_at=time.time(),
                    last_retry_at=time.time(),
                    func=original_token.func,
                    args=original_token.args,
                    kwargs=original_token.kwargs,
                    operation_type=operation_type
                )

                self.BACKUP_TOKENS[token_id] = backup

                tg_print('overflow', f'Creating backup for {token_id}')
                tg_print('overflow', f'Complexity: {complexity_level.name}', level='debug')
                tg_print('overflow',
                         f'Policy: {policy.max_retries} 'f'retries @ '
                         f'{policy.allocation_bump_percent * 100}% bumps', level='debug')
                tg_print('overflow',
                         f'Initial retry allocation: {backup.current_allocation_mb} MB', level='debug')

            # Create the retry token
            retry_metadata = TokenMetadata(
                operation_type=backup.operation_type,
                created_at=time.time(),
                tags={
                    'retry': 'true',
                    'retry_count': str(backup.current_retry_count),
                    'original_token': token_id,
                    'allocation_mb': str(backup.current_allocation_mb)
                }
            )

            retry_token = TaskToken(
                token_id=f"{token_id}_retry_{backup.current_retry_count}",
                func=backup.func,
                args=backup.args,
                kwargs=backup.kwargs,
                metadata=retry_metadata
            )

            # Inject directly into the token pool (BYPASS GATE!)
            self._inject_retry_to_pool(retry_token)

            self.total_retries_created += 1

            return retry_token

    @staticmethod
    def _inject_retry_to_pool(retry_token: TaskToken):
        """Inject a retry token directly into the global token pool and async queue."""

        # Add to pool's token dict
        global_token_pool.register_retry_token(retry_token)
        tg_print('overflow',
                 f'Injected retry token {retry_token.token_id} directly to pool', level='state')

    def record_success(self, token_id: str, execution_duration: float):
        """Record a successful retry outcome in aggregate statistics."""
        with self._backup_lock:
            if token_id in self.BACKUP_TOKENS:
                tg_print('overflow', f'Retry succeeded for {token_id}!')
                self.total_retries_succeeded += 1
                # Keep the backup entry for stats, mark as succeeded

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive guard statistics."""
        with self._backup_lock:
            active_backups = sum(1 for b in self.BACKUP_TOKENS.values() if b.can_retry())
            exhausted_backups = sum(1 for b in self.BACKUP_TOKENS.values() if not b.can_retry())

            return {
                'total_inspections': self.total_inspections,
                'total_retries_created': self.total_retries_created,
                'total_retries_succeeded': self.total_retries_succeeded,
                'total_retries_exhausted': self.total_retries_exhausted,
                'active_backups': active_backups,
                'exhausted_backups': exhausted_backups,
                'optimizer_stats': self.optimizer.get_all_stats()
            }

    # TODO: Display statuses for backup tokens inside the WebSocket dashboard.
    def print_backup_status(self):
        """Print readable backup token status."""
        with self._backup_lock:
            print()
            print("=" * 70)
            print("BACKUP TOKEN STATUS")
            print("=" * 70)
            print()
            print(f"Total Inspections: {self.total_inspections}")
            print(f"Retries Created: {self.total_retries_created}")
            print(f"Retries Succeeded: {self.total_retries_succeeded}")
            print(f"Retries Exhausted: {self.total_retries_exhausted}")
            print()

            if not self.BACKUP_TOKENS:
                print("No backup tokens")
                return

            print(f"Active Backups: {len(self.BACKUP_TOKENS)}")
            print()

            for token_id, backup in self.BACKUP_TOKENS.items():
                print(f"{token_id}:")
                print(f"  Complexity: {backup.complexity_level.name}")
                print(f"  Retries: {backup.current_retry_count}/{backup.max_retries}")
                print(f"  Allocation: {backup.current_allocation_mb} MB")
                print(f"  Bump rate: {backup.bump_percent * 100}%")
                print(f"  Age: {time.time() - backup.created_at:.1f}s")
                print()

            print("=" * 70)
