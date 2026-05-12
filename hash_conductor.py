# -*- coding: utf-8 -*-
# hash_conductor.py
"""
Hash Conductor — seed-based core domain anchoring for token call chains.

                          ┌──────────────────────────────────────┐
  Problem                 │  A lead token dispatches a chain of  │
                          │  child tokens.  Under normal load    │
                          │  balancing those children scatter    │
                          │  across cores, causing cross-domain  │
                          │  cache misses even when all the work │
                          │  is logically related.               │
                          └──────────────────────────────────────┘
                                           │
                          ┌──────────────────────────────────────┐
  Solution                │  When a lead token is charged, a     │
                          │  SHA-256 seed is derived from its    │
                          │  token_id and external_calls.  That  │
                          │  seed is pinned to whichever core    │
                          │  the lead lands on.  Any child token │
                          │  created during the lead's execution │
                          │  inherits the seed and is routed to  │
                          │  the same core domain automatically. │
                          └──────────────────────────────────────┘

Seed uniqueness
    Seed = SHA-256( token_id + ":" + freeze(external_calls) )
    Full 64-char hex digest — collision probability negligible at any
    realistic token volume.  token_id is included so two lead tokens
    with identical external_calls still get independent domains.

Parallel seeds
    Each lead call generates its own seed independently.  No special
    handling needed — parallel leads never share a seed.

Domain lifetime
    The seed domain is alive with charge() until the last token that
    carries the seed (lead or child) calls on_complete().  pending
    count starts at 1 (the lead itself) and increments for each
    registered child.  Release fires when the count reaches zero.

Thread model
    Child tokens are created inside the executor thread where the lead
    function runs.  The active seed is stored in a threading.local so
    it is visible to the task_token_guard decorator at token-creation
    time, before the submission crosses back to the event loop.

Integration points (see inline notes)
    1. put()              — charge leads; register children
    2. _execute_token()   — activate seed in executor thread wrapper
    3. task_token_guard() — stamp active seed onto new tokens
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .token_system import TaskToken

import hashlib
import threading
from typing import Dict, Optional

from .sticky_token import freeze, sticky_registry
from .tg_print import tg_print


# ---------------------------------------------------------------------------
# Thread-local active seed
# ---------------------------------------------------------------------------

# Set inside the executor thread just before the lead function runs.
# Read by task_token_guard to stamp the seed onto any token created
# during that execution.  Cleared after the function returns.
_active_seed: threading.local = threading.local()


def get_active_seed() -> Optional[str]:
    """Return the seed active in the current executor thread, or None."""
    return getattr(_active_seed, "value", None)


def _set_active_seed(seed: Optional[str]) -> None:
    _active_seed.value = seed


# ---------------------------------------------------------------------------
# HashConductor
# ---------------------------------------------------------------------------

class HashConductor:
    """Proactive seed-based core domain anchor for token call chains.

    Usage inside the queue:

        # At put() time — lead token (has external_calls):
        core_id = conductor.charge(token, candidate_core)

        # At put() time — child token (active seed present):
        core_id = conductor.register_child(token, candidate_core)

        # At execution time — wrap the callable:
        def _wrapped():
            conductor.activate(token)
            try:
                return token.func(*token.args, **token.kwargs)
            finally:
                conductor.deactivate()

        # At completion time:
        conductor.on_complete(token)

    Usage inside task_token_guard (token creation):

        seed = get_active_seed()
        if seed:
            token.metadata.tags["conductor_seed"] = seed
    """

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._cores:   Dict[str, int] = {}   # seed → pinned core_id
        self._pending: Dict[str, int] = {}   # seed → outstanding token count

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def generate_seed(token: TaskToken) -> str:
        """Derive a unique domain seed from a lead token.

        Uses the full SHA-256 digest of (token_id + freeze(external_calls))
        for collision-free uniqueness across any realistic token volume.
        """
        token_id       = getattr(token, "token_id", id(token))
        external_calls = (
            getattr(token.metadata, "external_calls", None)
            or token.metadata.tags.get("external_calls", "")
        )
        raw  = f"{token_id}:{freeze(external_calls)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def charge(self, token: TaskToken, candidate_core: int) -> int:
        """Assign a seed domain to a lead token and pin it to a core.

        Generates a fresh seed, stamps it onto the token's metadata,
        pins the seed to *candidate_core* via the sticky registry, and
        opens the pending count at 1 (the lead itself).

        Returns the actual pinned core (always candidate_core on first
        call; the sticky registry handles collisions if the same seed
        somehow appeared earlier).
        """
        seed = self.generate_seed(token)
        token.metadata.tags["conductor_seed"] = seed

        # Pin via the sticky registry using the seed as the op key.
        # Empty args — the seed itself is the unique identifier.
        core_id = sticky_registry.mark(seed, (), candidate_core)

        with self._lock:
            self._cores[seed]   = core_id
            self._pending[seed] = 1   # lead token counts toward the domain

        tg_print(
            "conductor",
            f"Charged   token={getattr(token, 'token_id', '?')}  "
            f"core={core_id}  seed={seed[:12]}…",
            level="dispatch",
        )
        return core_id

    def register_child(self, token: TaskToken, candidate_core: int) -> int:
        """Stamp the active seed onto a child token and route it to the domain.

        Called from put() when get_active_seed() returns a value during
        a child token's submission.  Increments the pending count so the
        domain stays alive until this child also completes.

        Returns the pinned core for the active seed, or candidate_core
        if the seed is no longer tracked (e.g. lead already released).
        """
        seed = token.metadata.tags.get("conductor_seed") or get_active_seed()
        if not seed:
            return candidate_core

        # seed is already stamped — no need to write it again
        with self._lock:
            if seed not in self._cores:
                tg_print(
                    "conductor",
                    f"Child fallthrough  token={getattr(token, 'token_id', '?')}  "
                    f"seed={seed[:12]}…  DOMAIN GONE — returning candidate_core={candidate_core}",
                    level="warn",
                )
                return candidate_core
            core_id = self._cores[seed]

        tg_print(
            "conductor",
            f"Registered child  token={getattr(token, 'token_id', '?')}  "
            f"core={core_id}  seed={seed[:12]}…  "
            f"pending={self._pending.get(seed, '?')}",
            level="dispatch",
        )
        return core_id

    def pre_register(self, seed: str):
        """Increment pending count at child token creation time.

        Called from task_token_guard while still in the executor thread,
        before the child crosses to the event loop for put(). Keeps the
        seed domain alive even if the lead completes before the child
        reaches register_child() in put().
        """
        with self._lock:
            if seed in self._pending:
                self._pending[seed] += 1

    @staticmethod
    def activate(token: TaskToken) -> Optional[str]:
        """Set the active seed in the executor thread before the lead runs.

        Must be called from *inside* the function passed to run_in_executor
        so the thread-local is set in the correct thread.

        Returns the seed, or None if this token carries no seed.
        """
        seed = token.metadata.tags.get("conductor_seed")
        _set_active_seed(seed)
        return seed

    @staticmethod
    def deactivate() -> None:
        """Clear the active seed after the lead function returns."""
        _set_active_seed(None)

    def on_complete(self, token: TaskToken):
        """Decrement the pending count for a token's seed.

        When the count reaches zero (lead + all children done) the seed
        domain is released from both the conductor and the sticky registry.
        """
        seed = token.metadata.tags.get("conductor_seed")
        if not seed:
            return

        # Added runtime guard to prevent stampping into seeds.
        assert isinstance(seed, str), f"conductor_seed tag must be str, got {type(seed)}"

        release = False
        with self._lock:
            if seed in self._pending:
                self._pending[seed] -= 1
                if self._pending[seed] <= 0:
                    release = True
                    self._cores.pop(seed, None)
                    self._pending.pop(seed, None)

        if release:
            sticky_registry.unmark(seed, ())
            tg_print(
                "conductor",
                f"Released  seed={seed[:12]}…",
                level="dispatch",
            )

    def snapshot(self) -> Dict[str, dict]:
        """Return a {seed_prefix: {core, pending}} snapshot for observability."""
        with self._lock:
            return {
                seed[:12]: {"core": core, "pending": self._pending.get(seed, 0)}
                for seed, core in self._cores.items()
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

conductor = HashConductor()