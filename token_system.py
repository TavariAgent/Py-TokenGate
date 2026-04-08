# -*- coding: utf-8 -*-
# token_system.py
"""
Core token primitives for token-managed execution.

This module defines token lifecycle state, token metadata, the task token
container, the token pool, and the user-facing decorator used to convert
ordinary callables into token-managed submissions.

Execution is not performed here. This module is responsible for request
capture, token creation, lifecycle bookkeeping, and admission handoff to
the coordinator/event-loop layer.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Callable, Any, Optional, Dict, ParamSpec, Generic, TypeVar


class TokenState(Enum):
    """Lifecycle states for a token-managed task.

    Tokens move from creation to admission, execution, and a terminal state.
    Terminal states are COMPLETED, FAILED, KILLED, and TIMEOUT.
    """
    CREATED = "created"  # Just created, in pool
    WAITING = "waiting"  # Waiting for admission
    ADMITTED = "admitted"  # Passed gate, in worker queue
    EXECUTING = "executing"  # Currently running on worker
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Execution failed
    KILLED = "killed"  # Admin killed this token
    TIMEOUT = "timeout"  # Exceeded time limit


@dataclass
class TokenMetadata:
    """Per-token metadata used for routing, timing, and observability.

    Stores the declared operation type, creation time, optional routing tags,
    and lifecycle timestamps populated as the token moves through admission
    and execution.
    """
    operation_type: str
    created_at: float
    created_by: str = "user"  # Track user
    max_execution_time: float = 300.0  # 5 min default
    tags: Dict[str, Any] = field(default_factory=dict)

    # Tracking
    admitted_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def age(self) -> float:
        """Return the token age in seconds since creation."""
        return time.time() - self.created_at

    def wait_time(self) -> Optional[float]:
        """Return seconds spent waiting for admission, if admitted."""
        if self.admitted_at:
            return self.admitted_at - self.created_at
        return None

    def execution_time(self) -> Optional[float]:
        """Return execution duration in seconds, if execution has started."""
        if self.started_at:
            end = self.completed_at or time.time()
            return end - self.started_at
        return None


T = TypeVar("T")

class TaskToken(Generic[T]):
    """Represents a deferred task submission managed by the token system.

    A TaskToken captures the target callable, its arguments, routing metadata,
    lifecycle state, and the future used to deliver the eventual result.

    The token is created immediately, but the wrapped callable is executed
    later by the coordinator/execution layer after admission.
    """
    def __init__(
            self,
            token_id: str,
            func: Callable,
            args: tuple[Any, ...],
            kwargs: dict,
            metadata: TokenMetadata
    ):
        self.state: TokenState = TokenState.CREATED
        self.token_id = token_id
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.metadata = metadata

        # State management
        self.on_state_change = None
        self._state_lock = threading.Lock()

        # Result delivery
        self._result_future: Future[T] = Future()
        self._result = None
        self._error = None

        # Admin control
        self._kill_requested = threading.Event()
        self._killed_reason: Optional[str] = None

    def __await__(self):
        """
        Makes this Token awaitable in asyncio loops.
        It wraps the underlying thread-safe concurrent.futures.Future.
        """
        return asyncio.wrap_future(self._result_future).__await__()

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

    def kill(self, reason: str = "admin_override"):
        """Request token termination and mark the result future as failed.

        Sets the internal kill flag, records the reason, and attempts to move the
        token into the KILLED state. If successful, waiting callers will receive
        TaskKilledException from the result future.

        Returns:
            True if the token was transitioned to KILLED, otherwise False.
        """
        self._kill_requested.set()
        self._killed_reason = reason

        if self.transition_state(TokenState.KILLED):
            # Set exception in future
            self._result_future.set_exception(
                TaskKilledException(f"Token killed: {reason}")
            )
            print(f"[KILL] Token {self.token_id} killed: {reason}")
            return True

        return False

    def is_killed(self) -> bool:
        """Check if this token has been killed."""
        return self._kill_requested.is_set()

    def set_result(self, result: Any):
        """Store a successful result and transition the token to COMPLETED."""
        self._result = result
        self.transition_state(TokenState.COMPLETED)
        self._result_future.set_result(result)

    def set_error(self, error: Exception):
        """Store an execution error and transition the token to FAILED."""
        self._error = error
        self.transition_state(TokenState.FAILED)
        self._result_future.set_exception(error)

    def get(self, timeout: Optional[float] = None) -> T:
        """Block until the token resolves or the timeout expires."""
        return self._result_future.result(timeout=timeout)


    def get_status(self) -> dict:
        """Return a snapshot of token state and timing information."""
        return {
            'token_id': self.token_id,
            'state': self.state.value,
            'operation_type': self.metadata.operation_type,
            'created_at': self.metadata.created_at,
            'age': self.metadata.age(),
            'wait_time': self.metadata.wait_time(),
            'execution_time': self.metadata.execution_time(),
            'tags': self.metadata.tags,
            'killed': self.is_killed(),
            'killed_reason': self._killed_reason,
            'has_result': self._result_future.done()
        }


class TaskKilledException(Exception):
    """Raised when a token is killed by admin."""
    pass

P = ParamSpec("P")
R = TypeVar("R")

class TokenPool:
    """Thread-safe registry and admission queue for task tokens.

    The pool owns created tokens, exposes inspection and administrative
    controls, and provides the async handoff point used by the admission loop.
    Token creation is synchronous and immediate; execution is deferred until
    a coordinator retrieves and admits the token.
    """
    def __init__(self):
        self.state: TokenState = TokenState.CREATED
        self.quarantine_mgr = None
        self.tokens: Dict[str, TaskToken] = {}
        self._lock = threading.Lock()

        self._token_queue: Optional[asyncio.Queue['TaskToken']] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.default_on_state_change = None

        # Metrics
        self.total_created = 0
        self.total_killed = 0
        self.total_admitted = 0

        # Admin controls
        self._paused = threading.Event()
        self._paused.set()  # Start unpaused

        # --- NEW PER-OPERATION STATE ---
        self._paused_operations: set[str] = set()
        self._paused_holding: Dict[str, list[TaskToken]] = {}

    def create_token(
            self,
            func: Callable[[P], R],
            args: tuple[Any, ...],
            kwargs: dict,
            operation_type: str,
            tags: Dict[str, Any] | None = None
    ) -> "TaskToken[R]":
        """Create, register, and enqueue a token for later admission.

        The token is created synchronously and stored in the pool immediately.
        If an event loop and async token queue are configured, the token is also
        submitted to the async admission queue.

        Returns:
            The created TaskToken instance.
        """
        self.total_created += 1
        token_id = f"{operation_type}_{time.time_ns()}"

        metadata = TokenMetadata(
            operation_type=operation_type,
            created_at=time.time(),
            tags=tags or {}
        )

        token = TaskToken(token_id, func, args, kwargs, metadata)

        with self._lock:
            self.tokens[token_id] = token

        token.transition_state(TokenState.WAITING)
        if self._event_loop:
            asyncio.run_coroutine_threadsafe(
                self._token_queue.put(token),
                self._event_loop
            )
        else:
            print(f"[POOL] WARNING: No event loop...")

        return token

    async def get_next_token(self):
        """Wait for and return the next token eligible for admission.

        If the pool is globally paused, this waits. If a specific token's
        operation_type is paused, it routes it to a holding area and grabs the next.
        """
        while True:
            # Wait if globally paused
            while not self._paused.is_set():
                await asyncio.sleep(0.1)

            # FIFO
            token = await self._token_queue.get()

            # 1. Skip if it was drained/killed while waiting in the queue
            if token.is_killed() or token.state == TokenState.KILLED:
                continue

            # 2. Check if this specific operation is paused
            with self._lock:
                is_op_paused = token.metadata.operation_type in self._paused_operations

            if is_op_paused:
                # Route to holding area and loop to get the next token
                with self._lock:
                    if token.metadata.operation_type not in self._paused_holding:
                        self._paused_holding[token.metadata.operation_type] = []
                    self._paused_holding[token.metadata.operation_type].append(token)
                continue

            return token

    def get_all_tokens(self) -> Dict[str, TaskToken]:
        """Return a shallow snapshot of all registered tokens."""
        with self._lock:
            return dict(self.tokens)

    def get_tokens_by_state(self, state: TokenState) -> list[TaskToken]:
        """Return all tokens currently in the requested lifecycle state."""
        with self._lock:
            return [t for t in self.tokens.values() if t.state == state]

    def get_tokens_by_operation(self, operation_type: str) -> list[TaskToken]:
        """Return all tokens matching the given operation type."""
        with self._lock:
            return [
                t for t in self.tokens.values()
                if t.metadata.operation_type == operation_type
            ]

    def kill_token(self, token_id: str, reason: str = "admin_override") -> bool:
        """Kill a registered token by id."""
        with self._lock:
            if token_id in self.tokens:
                if self.tokens[token_id].kill(reason):
                    self.total_killed += 1
                    return True
        return False

    def kill_all_by_operation(self, operation_type: str, reason: str = "admin_bulk_kill"):
        """Kill all registered tokens matching an operation type."""
        tokens = self.get_tokens_by_operation(operation_type)
        killed = 0
        for token in tokens:
            if token.kill(reason):
                killed += 1

        self.total_killed += killed
        print(f"[POOL] Killed {killed} tokens of type {operation_type}")
        return killed

    def pause(self, operation_type: str = None, reason: str = "admin_pause"):
        """Pause token admission globally or for a specific operation type."""
        if operation_type:
            with self._lock:
                self._paused_operations.add(operation_type)
            print(f"[POOL] PAUSED operation: {operation_type} ({reason})")
        else:
            self._paused.clear()
            print(f"[POOL] PAUSED globally ({reason}) - tokens will accumulate")

    def resume(self, operation_type: str = None, reason: str = "admin_resume"):
        """Resume token admission globally or for a specific operation type."""
        if operation_type:
            tokens_to_requeue = []
            with self._lock:
                self._paused_operations.discard(operation_type)
                # Retrieve held tokens
                if operation_type in self._paused_holding:
                    tokens_to_requeue = self._paused_holding.pop(operation_type)

            # Re-insert held tokens back into the async admission queue
            if self._event_loop and tokens_to_requeue:
                for token in tokens_to_requeue:
                    asyncio.run_coroutine_threadsafe(
                        self._token_queue.put(token),
                        self._event_loop
                    )
            print(
                f"[POOL] RESUMED operation: {operation_type} ({reason}) - requeued {len(tokens_to_requeue)} held tokens")
        else:
            self._paused.set()
            print(f"[POOL] RESUMED globally ({reason}) - tokens will admit")

    def drain(self, operation_type: str = None, reason: str = "admin_drain") -> int:
        """Kill waiting tokens, either globally or matching a specific operation type."""
        waiting = self.get_tokens_by_state(TokenState.WAITING)
        killed = 0

        for token in waiting:
            if operation_type is None or token.metadata.operation_type == operation_type:
                if token.kill(reason):
                    killed += 1

        self.total_killed += killed
        print(f"[POOL] DRAINED - killed {killed} waiting tokens (op_type={operation_type})")
        return killed

    def get_stats(self) -> dict:
        """Get pool statistics for dashboard."""
        tokens_by_state = {}
        for state in TokenState:
            tokens_by_state[state.value] = len(self.get_tokens_by_state(state))

        return {
            'total_created': self.total_created,
            'total_killed': self.total_killed,
            'total_admitted': self.total_admitted,
            'current_tokens': len(self.tokens),
            'tokens_by_state': tokens_by_state,
            'paused': not self._paused.is_set()
        }

    @staticmethod
    def _get_loop():
        """Get or create an event loop."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

# ============================================================================
# DECORATOR - The user-facing API
# ============================================================================
def task_token_guard(
    operation_type: str,
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

            final_tags = dict(tags) if tags else {}  # copy, don't mutate shared dict
            final_func = func

            # Check Guard House for spike detection
            if hasattr(global_token_pool, 'spike_detector') and global_token_pool.spike_detector:
                spike_detector = global_token_pool.spike_detector
                quarantine_mgr = global_token_pool.quarantine_mgr

                should_q, deviation, reason = spike_detector.should_quarantine(
                    func.__name__,
                    metrics
                )

                if should_q:
                    # QUARANTINE!
                    quarantine_mgr.quarantine_token(
                        token_id=f"{operation_type}_{time.time_ns()}",
                        method_name=func.__name__,
                        operation_type=operation_type,
                        predicted_complexity=metrics.complexity_score,
                        historical_avg_complexity=spike_detector.guard_house.get_reputation(func.__name__).avg_complexity_score,
                        deviation_percent=deviation,
                        args=args,
                        kwargs=kwargs,
                        reason=reason
                    )

                    # Raise exception to block execution
                    raise TokenQuarantinedException(
                        f"Token quarantined due to complexity spike.\n"
                        f"Method: {func.__name__}\n"
                        f"Deviation: {deviation * 100:.1f}%\n"
                        f"Reason: {reason}\n"
                        f"Check quarantine.json for details."
                    )

            # ================================================================
            # Check for storage speed tier tag
            # ================================================================

            if 'storage_speed' in final_tags:
                # Storage throttling requested!
                speed_tier = final_tags['storage_speed']

                from .storage_throttle import get_storage_throttle

                throttle_mgr = get_storage_throttle()

                # Create throttled wrapper
                def throttled_func(*inner_args, **inner_kwargs):
                    # Execute with storage throttling
                    return throttle_mgr.throttle(speed_tier, func, *inner_args, **inner_kwargs)

                final_func = throttled_func

                # Add metadata to tags
                final_tags['storage_throttled'] = 'True'
                final_tags['throttle_tier'] = speed_tier

                # Optional: Log first time we see this operation
                if not hasattr(wrapper, '_storage_logged'):
                    print(f"[STORAGE] Auto-throttling enabled: '{operation_type}' → {speed_tier} tier")
                    wrapper._storage_logged = True

            # ================================================================
            # Create token (with potentially throttled function)
            # ================================================================
            token = global_token_pool.create_token(
                func=final_func,
                args=args,
                kwargs=kwargs,
                operation_type=operation_type,
                tags=final_tags
            )

            token.metadata.tags["complexity_score"] = metrics.complexity_score

            cb = getattr(global_token_pool, "default_on_state_change", None)
            if cb is not None and getattr(token, "on_state_change", None) is None:
                token.on_state_change = cb

            return token

        return wrapper

    return decorator


# Global instance (created on import)
global_token_pool = TokenPool()