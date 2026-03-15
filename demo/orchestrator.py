"""
orchestrator.py — Random mixed workload orchestrator for Proof 3.

Simulates a variable-load environment comparable to a lower-level cloud
server handling heterogeneous requests. Operations are chosen randomly
from the full CPU + IO pool, submitted in random batch sizes, at random
intervals between 0.01s and 1.0s.

This is the mixed workload proof — it demonstrates that CPU-bound and
I/O-bound operations can be coordinated together without explicit
separation by the caller. The architecture routes each task correctly
based on its tags, not its position in the submission order.
"""

import os
import time
import random
import tempfile
import statistics
from dataclasses import dataclass, field
from typing import Callable

from .cpu_ops import (
    trivial_operation, simple_operation, string_operation,
    moderate_operation, prime_operation, complex_operation,
    heavy_operation, fibonacci_operation,
)
from .io_ops import write_json_fast, append_log_slow, write_blob_moderate


# ── Operation registry ────────────────────────────────────────────────────────

def _make_ops(base_dir: str) -> list[tuple[Callable, Callable]]:
    """
    Build the full operation pool with argument generators.
    Each entry is (function, arg_generator) — arg_generator returns
    a single arg or tuple of args for that function.
    """
    json_dir = os.path.join(base_dir, "json")
    log_path = os.path.join(base_dir, "logs", "mixed.log")
    blob_dir = os.path.join(base_dir, "blob")

    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(blob_dir, exist_ok=True)

    return [
        # CPU — light
        (trivial_operation,  lambda: random.randint(1, 100)),
        (simple_operation,   lambda: random.randint(100, 500)),
        (string_operation,   lambda: random.randint(10, 40)),
        # CPU — medium
        (moderate_operation, lambda: random.randint(1000, 8000)),
        (prime_operation,    lambda: random.choice([97,101,103,107,109,113,
                                                    127,131,137,139,100,102])),
        (complex_operation,  lambda: random.randint(100, 600)),
        # CPU — heavy
        (heavy_operation,    lambda: random.randint(500, 2000)),
        (fibonacci_operation,lambda: random.randint(100, 300)),
        # IO — light
        (write_json_fast,    lambda: (
            os.path.join(json_dir, f"r_{time.time_ns()}.json"),
            {"ts": time.time(), "val": random.random()}
        )),
        # IO — heavy
        (append_log_slow,    lambda: (
            log_path,
            f"[{time.time():.3f}] mixed entry {random.randint(0, 9999)}"
        )),
        # IO — medium
        (write_blob_moderate,lambda: (
            os.path.join(blob_dir, f"b_{time.time_ns()}.bin"),
            random.randint(4, 16)
        )),
    ]


# ── Stats ─────────────────────────────────────────────────────────────────────

@dataclass
class OrchestratorStats:
    submitted: int = 0
    resolved: int = 0
    failed: int = 0
    latencies: list = field(default_factory=list)
    op_counts: dict = field(default_factory=dict)

    def record(self, op_name: str, latency: float):
        self.resolved += 1
        self.latencies.append(latency)
        self.op_counts[op_name] = self.op_counts.get(op_name, 0) + 1

    def summary(self) -> dict:
        lat = sorted(self.latencies)
        return {
            'submitted':    self.submitted,
            'resolved':     self.resolved,
            'failed':       self.failed,
            'resolve_rate': f"{self.resolved / self.submitted * 100:.1f}%" if self.submitted else "N/A",
            'p50':          f"{statistics.median(lat):.3f}s" if lat else "N/A",
            'p95':          f"{lat[int(len(lat) * 0.95)]:.3f}s" if len(lat) >= 20 else "N/A",
            'max':          f"{max(lat):.3f}s" if lat else "N/A",
            'op_counts':    self.op_counts,
        }


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_mixed_orchestrator(
    duration_seconds: float = 60.0,
    min_batch: int = 10,
    max_batch: int = 20,
    min_interval: float = 0.01,
    max_interval: float = 1.0,
):
    """
    Submit random mixed batches at random intervals for duration_seconds.

    Every batch contains a random mix of CPU and IO operations chosen
    from the full operation pool. The architecture routes each task
    independently based on its weight tag — the caller makes no routing
    decisions at all.

    Parameters
    ----------
    duration_seconds : Total run time in seconds.
    min_batch        : Minimum tasks per wave.
    max_batch        : Maximum tasks per wave.
    min_interval     : Fastest submission interval (seconds).
    max_interval     : Slowest submission interval (seconds).
    """
    base_dir = tempfile.mkdtemp(prefix="threads_mixed_")
    ops = _make_ops(base_dir)
    stats = OrchestratorStats()

    print(f"\n{'=' * 70}")
    print("MIXED WORKLOAD ORCHESTRATOR")
    print(f"{'=' * 70}")
    print(f"  Duration  : {duration_seconds}s")
    print(f"  Batch size: {min_batch}–{max_batch} tasks")
    print(f"  Interval  : {min_interval}–{max_interval}s")
    print(f"  Op pool   : {len(ops)} operation types (CPU + IO)")
    print(f"  Output dir: {base_dir}")
    print()

    start = time.perf_counter()
    wave = 0

    while time.perf_counter() - start < duration_seconds:
        wave += 1
        batch_size = random.randint(min_batch, max_batch)
        tokens = []
        submit_times = []

        # Submit entire batch before resolving any
        for _ in range(batch_size):
            func, arg_gen = random.choice(ops)
            arg = arg_gen()
            try:
                t = time.perf_counter()
                token = func(*arg) if isinstance(arg, tuple) else func(arg)
                tokens.append((token, func.__name__, t))
                stats.submitted += 1
            except Exception as e:
                stats.failed += 1
                print(f"  [ORCH] ✗ Submission failed: {e}")

        # Resolve batch
        for token, op_name, submit_at in tokens:
            try:
                result = token.get(timeout=60)
                latency = time.perf_counter() - submit_at
                stats.record(op_name, latency)
            except Exception as e:
                stats.failed += 1
                print(f"  [ORCH] ✗ {op_name} failed: {type(e).__name__}: {e}")

        # Progress every 10 waves
        if wave % 10 == 0:
            elapsed = time.perf_counter() - start
            s = stats.summary()
            print(f"  Wave {wave:4d} | {elapsed:6.1f}s | "
                  f"Resolved: {s['resolved']:5d} | "
                  f"p50: {s['p50']} | p95: {s['p95']}")

        # Random interval before next wave
        interval = random.uniform(min_interval, max_interval)
        time.sleep(interval)

    # ── Final report ─────────────────────────────────────────────────────────
    total_elapsed = time.perf_counter() - start
    s = stats.summary()

    print(f"\n{'=' * 70}")
    print("MIXED ORCHESTRATOR COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Total wall-clock : {total_elapsed:.1f}s")
    print(f"  Waves            : {wave}")
    print(f"  Submitted        : {s['submitted']}")
    print(f"  Resolved         : {s['resolved']}  ({s['resolve_rate']})")
    print(f"  Failed           : {s['failed']}")
    print(f"  Latency p50      : {s['p50']}")
    print(f"  Latency p95      : {s['p95']}")
    print(f"  Latency max      : {s['max']}")
    print()
    print("  Operations dispatched:")
    for op, count in sorted(s['op_counts'].items(), key=lambda x: -x[1]):
        bar = '█' * (count // 5)
        print(f"    {op:35s} {count:5d}  {bar}")

    return stats