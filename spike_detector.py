# -*- coding: utf-8 -*-
# spike_detector.py
"""
Spike Detector & Quarantine Manager

"Debug their bad behavior BEFORE it executes."

Detects anomalous token complexity by comparing predicted complexity
against historical patterns. Quarantines suspicious tokens for admin review.

Philosophy:
- Malicious users try huge payloads → Spike detected → Quarantined
- Accidental huge inputs → Spike detected → Data preserved for review
- Legitimate spikes → Admin can approve and replay

Features:
1. Complexity spike detection (deviation threshold)
2. Token quarantine with preserved arguments
3. JSON-based quarantine log for admin review
4. Replay system for approved tokens
5. DOS resistance through early detection

Integration:
- Called during token creation (before execution)
- Uses CodeInspector for predicted complexity
- Uses GuardHouse for historical patterns
- Blocks execution if spike detected
"""

import json
import time
import threading
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime

from tg_print import tg_print
from .code_inspector import CodeMetrics


@dataclass
class QuarantinedToken:
    """
    Record of a quarantined token.

    Preserves all information needed to replay or analyze.
    """
    token_id: str
    method_name: str

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
    operation_type: Optional[str] = None
    admin_reviewed: bool = False
    admin_decision: Optional[str] = None  # 'approved', 'rejected', 'modified'

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def get_timestamp_str(self) -> str:
        """Get human-readable timestamp."""
        return datetime.fromtimestamp(self.timestamp).strftime('%Y-%m-%d %H:%M:%S')


class TokenQuarantinedException(Exception):
    """Raised when a token is quarantined due to spike detection."""
    pass


class SpikeDetector:
    """
    Detects complexity spikes in token creation.

    Compares predicted complexity against historical averages
    to identify anomalous inputs.
    """

    # Default thresholds
    DEFAULT_SPIKE_THRESHOLD = 0.50  # 50% deviation
    DEFAULT_EXTREME_THRESHOLD = 2.0  # 200% deviation (definitely malicious/accidental)
    MIN_HISTORICAL_SAMPLES = 5  # Need at least 5 samples for a reliable baseline

    def __init__(
            self,
            guard_house,
            spike_threshold: float = DEFAULT_SPIKE_THRESHOLD,
            extreme_threshold: float = DEFAULT_EXTREME_THRESHOLD,
            min_samples: int = MIN_HISTORICAL_SAMPLES
    ):
        """
        Initialize spike detector.

        Args:
            guard_house: GuardHouse instance for historical data
            spike_threshold: Deviation % to trigger warning (default 50%)
            extreme_threshold: Deviation % to auto-quarantine (default 200%)
            min_samples: Minimum historical samples needed
        """
        self.guard_house = guard_house
        self.spike_threshold = spike_threshold
        self.extreme_threshold = extreme_threshold
        self.min_samples = min_samples

        tg_print('guard', f'SpikeDetector initialized  '
                          f'spike={spike_threshold * 100}%  '
                          f'extreme={extreme_threshold * 100}%  '
                          f'min_samples={min_samples}')

    def check_for_spike(
            self,
            method_name: str,
            predicted_metrics: CodeMetrics
    ) -> tuple[bool, float, str]:
        """
        Check if predicted complexity represents a spike.

        Args:
            method_name: Name of the method
            predicted_metrics: Predicted metrics from CodeInspector

        Returns:
            (is_spike, deviation, reason)
        """
        # Get historical reputation
        reputation = self.guard_house.get_reputation(method_name)

        # No historical data = can't detect spike (allow execution)
        if not reputation:
            return False, 0.0, "No historical data"

        # Not enough samples = unreliable baseline (allow execution)
        if reputation.total_attempts < self.min_samples:
            return False, 0.0, f"Insufficient samples ({reputation.total_attempts}/{self.min_samples})"

        # Get historical average complexity
        historical_avg = getattr(reputation, 'avg_complexity_score', None)

        # No complexity tracking yet (allow execution)
        if historical_avg is None or historical_avg == 0.0:
            return False, 0.0, "No complexity baseline"

        # Calculate deviation
        predicted_complexity = predicted_metrics.complexity_score
        deviation = (predicted_complexity - historical_avg) / historical_avg

        # Check thresholds
        if deviation >= self.extreme_threshold:
            reason = f"EXTREME spike: {deviation * 100:.1f}% deviation (predicted: {predicted_complexity:.1f}, avg: {historical_avg:.1f})"
            return True, deviation, reason

        elif deviation >= self.spike_threshold:
            reason = f"Moderate spike: {deviation * 100:.1f}% deviation (predicted: {predicted_complexity:.1f}, avg: {historical_avg:.1f})"
            return True, deviation, reason

        # No spike detected
        return False, deviation, "Within normal range"

    def should_quarantine(
            self,
            method_name: str,
            predicted_metrics: CodeMetrics,
            auto_quarantine_extreme: bool = True
    ) -> tuple[bool, float, str]:
        """
        Determine if the token should be quarantined.

        Args:
            method_name: Name of the method
            predicted_metrics: Predicted metrics
            auto_quarantine_extreme: Auto-quarantine extreme spikes

        Returns:
            (should_quarantine, deviation, reason)
        """
        is_spike, deviation, reason = self.check_for_spike(method_name, predicted_metrics)

        if not is_spike:
            return False, deviation, reason

        # Auto-quarantine extreme spikes
        if auto_quarantine_extreme and deviation >= self.extreme_threshold:
            return True, deviation, f"AUTO-QUARANTINE: {reason}"

        # Moderate spikes - quarantine for review
        if deviation >= self.spike_threshold:
            return True, deviation, f"REVIEW REQUIRED: {reason}"

        return False, deviation, reason


class QuarantineManager:
    """
    Manages quarantined tokens.

    Saves quarantined tokens to JSON, preserves arguments,
    and provides an admin review interface.
    """

    def __init__(self, quarantine_file: Path = None):
        """
        Initialize quarantine manager.

        Args:
            quarantine_file: Path to quarantine JSON file
        """
        self.quarantine_file = quarantine_file or Path("quarantine.json")
        self.quarantined_tokens: List[QuarantinedToken] = []
        self._lock = threading.Lock()

        # Statistics
        self.total_quarantined = 0
        self.total_approved = 0
        self.total_rejected = 0

        # Load existing quarantine if exists
        self._load_quarantine()

        tg_print('guard', f'QuarantineManager initialized  '
                          f'file={self.quarantine_file}  loaded={len(self.quarantined_tokens)} tokens')

    def _load_quarantine(self):
        """Load existing quarantine file."""
        if not self.quarantine_file.exists():
            return

        try:
            with open(self.quarantine_file, 'r') as f:
                data = json.load(f)

            for entry in data:
                token = QuarantinedToken(**entry)
                self.quarantined_tokens.append(token)

                # Update stats
                if token.admin_reviewed:
                    if token.admin_decision == 'approved':
                        self.total_approved += 1
                    elif token.admin_decision == 'rejected':
                        self.total_rejected += 1

        except Exception as e:
            tg_print('guard', f'Error loading quarantine file: {e}', level='error')

    def _save_quarantine(self):
        """Save quarantine to JSON file."""
        try:
            data = [token.to_dict() for token in self.quarantined_tokens]

            with open(self.quarantine_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            tg_print('guard', f'Error saving quarantine file: {e}', level='error')

    def quarantine_token(
            self,
            token_id: str,
            method_name: str,
            predicted_complexity: float,
            historical_avg_complexity: float,
            deviation_percent: float,
            args: tuple[Any, ...],
            kwargs: dict,
            reason: str,
            operation_type: Optional[str] = None
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

            tg_print('guard', f'QUARANTINED: {token_id}  '
                              f'method={method_name}  deviation={deviation_percent * 100:.1f}%', level='warn')
            tg_print('guard', f'Reason: {reason}', level='warn')

            return quarantined

    @staticmethod
    def _summarize_args(args: tuple[Any, ...],) -> str:
        """Create a brief summary of args."""
        if not args:
            return "No args"

        summary_parts = []
        for i, arg in enumerate(args):
            if isinstance(arg, (list, tuple)):
                summary_parts.append(f"arg{i}: {type(arg).__name__}[{len(arg)}]")
            elif isinstance(arg, dict):
                summary_parts.append(f"arg{i}: dict[{len(arg)}]")
            elif isinstance(arg, str):
                preview = arg[:50] + "..." if len(arg) > 50 else arg
                summary_parts.append(f"arg{i}: '{preview}'")
            else:
                summary_parts.append(f"arg{i}: {type(arg).__name__}")

        return ", ".join(summary_parts)

    @staticmethod
    def _serialize_args(args: tuple[Any, ...],) -> str:
        """Serialize args to string (with size limits)."""
        try:
            # Try JSON serialization
            serialized = json.dumps(args)

            # Truncate if huge
            if len(serialized) > 10000:
                return serialized[:10000] + "... [TRUNCATED]"

            return serialized
        except:
            return f"<Non-serializable: {type(args)}>"

    @staticmethod
    def _summarize_kwargs(kwargs: dict) -> str:
        """Create brief summary of kwargs."""
        if not kwargs:
            return "No kwargs"

        summary_parts = []
        for key, value in kwargs.items():
            if isinstance(value, (list, tuple)):
                summary_parts.append(f"{key}: {type(value).__name__}[{len(value)}]")
            elif isinstance(value, dict):
                summary_parts.append(f"{key}: dict[{len(value)}]")
            else:
                summary_parts.append(f"{key}: {type(value).__name__}")

        return ", ".join(summary_parts)

    @staticmethod
    def _serialize_kwargs(kwargs: dict) -> str:
        """Serialize kwargs to string."""
        try:
            serialized = json.dumps(kwargs)

            if len(serialized) > 10000:
                return serialized[:10000] + "... [TRUNCATED]"

            return serialized
        except:
            return f"<Non-serializable: {type(kwargs)}>"

    def get_pending_review(self) -> List[QuarantinedToken]:
        """Get tokens pending admin review."""
        with self._lock:
            return [
                token for token in self.quarantined_tokens
                if not token.admin_reviewed
            ]

    def approve_token(self, token_id: str) -> bool:
        """
        Approve a quarantined token for replay.

        Args:
            token_id: Token to approve

        Returns:
            True if approved, False if not found
        """
        with self._lock:
            for token in self.quarantined_tokens:
                if token.token_id == token_id:
                    token.admin_reviewed = True
                    token.admin_decision = 'approved'
                    self.total_approved += 1
                    self._save_quarantine()

                    tg_print('guard', f'Token approved: {token_id}')
                    return True

        return False

    def reject_token(self, token_id: str) -> bool:
        """
        Reject a quarantined token.

        Args:
            token_id: Token to reject

        Returns:
            True if rejected, False if not found
        """
        with self._lock:
            for token in self.quarantined_tokens:
                if token.token_id == token_id:
                    token.admin_reviewed = True
                    token.admin_decision = 'rejected'
                    self.total_rejected += 1
                    self._save_quarantine()

                    tg_print('guard', f'Token rejected: {token_id}', level='warn')
                    return True

        return False

    # TODO: Add quarantine report too the WebSocket dashboard
    def print_quarantine_report(self):
        """Print human-readable quarantine report."""
        with self._lock:
            print()
            print("=" * 70)
            print("QUARANTINE MANAGER - ADMIN REVIEW DASHBOARD")
            print("=" * 70)
            print()

            print(f"Statistics:")
            print(f"  Total quarantined: {self.total_quarantined}")
            print(f"  Approved: {self.total_approved}")
            print(f"  Rejected: {self.total_rejected}")
            print(f"  Pending review: {len(self.get_pending_review())}")
            print()

            # Pending review
            pending = self.get_pending_review()
            if pending:
                print("PENDING REVIEW:")
                for token in pending[:10]:  # Show top 10
                    print(f"  ├─ {token.token_id}")
                    print(f"  │  Method: {token.method_name}")
                    print(f"  │  Deviation: {token.deviation_percent:.1f}%")
                    print(f"  │  Time: {token.get_timestamp_str()}")
                    print(f"  │  Args: {token.args_summary}")
                    print(f"  │  Reason: {token.quarantine_reason}")
                    print(f"  │")

                if len(pending) > 10:
                    print(f"  └─ ... and {len(pending) - 10} more")
                print()

            print("=" * 70)

    def get_stats(self) -> Dict:
        """Get quarantine statistics."""
        with self._lock:
            return {
                'total_quarantined': self.total_quarantined,
                'total_approved': self.total_approved,
                'total_rejected': self.total_rejected,
                'pending_review': len(self.get_pending_review()),
                'quarantine_file': str(self.quarantine_file)
            }