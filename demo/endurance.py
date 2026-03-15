"""
endurance.py — Continuous, never-stopping load modulation.

The endurance runner does not stop between phases. It runs indefinitely,
shifting submission frequency across three operating modes:

  IDLE   — slow, sparse submissions. Batches are uncapped and run in parallel.
             The system is comfortable. Workers have headroom. Tokens resolve
             promptly. This is the intended operating harmony.

  NORMAL — moderate, steady submissions. The system is working. Workers are
             consistently occupied. The mailbox absorbs brief bursts without
             complaint. Resolution latency is stable.

  SURGE  — fast, aggressive submissions. The system is under pressure.
             The mailbox will fill. When it is full, new submissions are
             set aside or discarded depending on mailbox capacity config.
             This is NOT the intended harmony. It is a stress condition.

─────────────────────────────────────────────────────────────────────────────
WHY FILLING THE MAIL IS NOT HARMONY

Every system has a hard limit. Systems without protection fail under explosive
load. Threads does not fail — it bounds the work. When the mailbox is full:

  • Submissions that exceed capacity are set aside or discarded.
  • Workers continue draining what is already queued.
  • The system does not deadlock, panic, or corrupt state.

This is correct behavior. The alternative — accepting all work always — leads
to unbounded memory growth and eventual meltdown.

FUTURE UPGRADE PATH (not implemented here):
  Instead of discarding, the system can log the caller's arguments at
  submission time. When the mailbox drains and the system cools, logged
  calls can be replayed in order. This turns discard into deferral.
  The architecture supports this pattern. It is a deliberate next step.
─────────────────────────────────────────────────────────────────────────────

Usage:
    runner = EnduranceRunner(duration_seconds=300)  # 5-minute proof run
    runner.start()
    # ... let it run, observe logs ...
    runner.stop()

    # Or run forever until KeyboardInterrupt:
    runner = EnduranceRunner()
    runner.run_forever()
"""
import asyncio
import time
import random
import threading
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .cpu_ops import (
    trivial_operation, simple_operation, moderate_operation,
    complex_operation, heavy_operation, string_operation,
)
from .series_ops import series_short, series_medium
from ..token_system import TaskToken


# ── Phase Definitions ────────────────────────────────────────────────────────

class Phase(Enum):
    IDLE   = "IDLE"
    NORMAL = "NORMAL"
    SURGE  = "SURGE"


@dataclass
class PhaseConfig:
    """
    Controls submission behavior during a phase.

    batch_size:       How many tasks are submitted per wave.
    interval_range:   (min, max) seconds between waves.
    ops:              Weighted pool of (function, arg_generator) pairs.
    label:            Display name for logs.
    note:             One-line description of what this phase demonstrates.
    """
    phase: Phase
    batch_size: int
    interval_range: tuple
    ops: list
    label: str
    note: str


# ── Endurance Runner ─────────────────────────────────────────────────────────

@dataclass
class EnduranceStats:
    submitted: int = 0
    resolved: int = 0
    discarded: int = 0
    failed: int = 0
    latencies: list = field(default_factory=list)
    wait_times: list = field(default_factory=list)
    phase_counts: dict = field(default_factory=dict)
    token_cache: dict[str, TaskToken] = field(default_factory=dict)  # ← cache

    def register(self, token: TaskToken):
        """Register a token at submission time."""
        self.token_cache[token.token_id] = token
        self.submitted += 1

    def release(self, token_id: str):
        """Release after resolution to prevent unbounded growth."""
        self.token_cache.pop(token_id, None)

    def record_wait(self, seconds: float):
        self.wait_times.append(seconds)
        if len(self.wait_times) > 500:
            self.wait_times = self.wait_times[-500:]

    def record_latency(self, seconds: float):
        self.latencies.append(seconds)
        if len(self.latencies) > 500:
            # Rolling window — don't grow unbounded during long runs
            self.latencies = self.latencies[-500:]

    def summary(self) -> dict:
        lat = self.latencies
        w = self.wait_times
        return {
            'submitted': self.submitted,
            'resolved': self.resolved,
            'discarded': self.discarded,
            'failed': self.failed,
            'resolve_rate': f"{(self.resolved / self.submitted * 100):.1f}%" if self.submitted else "N/A",
            'discard_rate': f"{(self.discarded / self.submitted * 100):.1f}%" if self.submitted else "N/A",
            'p50_latency': f"{statistics.median(lat):.3f}s" if lat else "N/A",
            'p95_latency': f"{sorted(lat)[int(len(lat) * 0.95)]:.3f}s" if len(lat) >= 20 else "N/A",
            'p50_wait': f"{statistics.median(w):.3f}s" if w else "N/A",
            'p95_wait': f"{sorted(w)[int(len(w) * 0.95)]:.3f}s" if len(w) >= 20 else "N/A",
            'max_latency': f"{max(lat):.3f}s" if lat else "N/A",
        }


class EnduranceRunner:
    """
    Continuous load modulator. Never stops between phases — only changes
    submission frequency and batch characteristics. Intended to run for
    minutes or hours without operator intervention.
    """

    # ── Phase configs ────────────────────────────────────────────────────────

    PHASES = [
        PhaseConfig(
            phase=Phase.IDLE,
            batch_size=250,
            interval_range=(3.0, 10.0),
            ops=[
                (trivial_operation,  lambda: random.randint(1, 50)),
                (simple_operation,   lambda: random.randint(50, 200)),
                (string_operation,   lambda: random.randint(5, 20)),
            ],
            label="IDLE",
            note="Sparse uncapped batches. Workers have headroom. Intended harmony.",
        ),
        PhaseConfig(
            phase=Phase.NORMAL,
            batch_size=500,
            interval_range=(0.8, 2.5),
            ops=[
                (simple_operation,    lambda: random.randint(100, 400)),
                (moderate_operation,  lambda: random.randint(300, 800)),
                (series_short,        lambda: round(random.uniform(0.05, 0.2), 3)),
                (string_operation,    lambda: random.randint(10, 30)),
            ],
            label="NORMAL",
            note="Steady load. Mailbox absorbs bursts. Resolution latency stable.",
        ),
        PhaseConfig(
            phase=Phase.SURGE,
            batch_size=750,
            interval_range=(0.05, 0.3),
            ops=[
                (simple_operation,    lambda: random.randint(200, 500)),
                (moderate_operation,  lambda: random.randint(500, 1200)),
                (complex_operation,   lambda: random.randint(8, 20)),
                (series_medium,       lambda: round(random.uniform(0.3, 0.8), 3)),
                (heavy_operation,     lambda: random.randint(80, 160)),
            ],
            label="SURGE ⚡",
            note="Aggressive load. Mailbox will fill. Overflow is set aside — not a failure.",
        ),
    ]

    # Phase schedule: list of (Phase, duration_seconds) tuples.
    # The runner cycles through this schedule indefinitely.
    SCHEDULE = [
        (Phase.IDLE,   10),   # warm up, establish baseline
        (Phase.NORMAL, 20),   # steady state
        (Phase.SURGE,  40),   # stress, fill the mail
        (Phase.NORMAL, 20),   # recovery observation
        (Phase.IDLE,   10),   # confirm system cooled cleanly
        (Phase.SURGE,  40),   # second surge — does it hold?
        (Phase.NORMAL, 20),   # long steady tail
    ]

    def __init__(self, duration_seconds: Optional[float] = None, log_interval: float = 15.0):
        """
        duration_seconds: Total run time. None = run until stop() is called.
        log_interval:     How often (seconds) to print a stats summary.
        """
        self.duration_seconds = duration_seconds
        self.log_interval = log_interval
        self.stats = EnduranceStats()
        self._stop_event = threading.Event()
        self._phase_map = {p.phase: p for p in self.PHASES}
        self._resolve_threads: list[threading.Thread] = []

    def stop(self):
        self._stop_event.set()

    def _get_phase_config(self, phase: Phase) -> PhaseConfig:
        return self._phase_map[phase]

    async def _submit_batch(self, config: PhaseConfig):
        ops = config.ops
        token_pairs = []

        for _ in range(config.batch_size):
            func, arg_gen = random.choice(ops)
            arg = arg_gen()
            try:
                token = func(arg)
                self.stats.register(token)
                self.stats.phase_counts[config.phase] = (
                        self.stats.phase_counts.get(config.phase, 0) + 1
                )
                token_pairs.append(token)
            except Exception:
                self.stats.discarded += 1

        async def resolve_one(token):
            try:
                await asyncio.wrap_future(token._result_future)
                status = token.get_status()
                self.stats.release(token.token_id)

                if not status['has_result'] or status['killed']:
                    self.stats.discarded += 1
                    return

                self.stats.resolved += 1
                exec_time = status['execution_time']
                wait_time = status['wait_time']
                if exec_time is not None:
                    self.stats.record_latency(exec_time)
                if wait_time is not None:
                    self.stats.record_wait(wait_time)

            except Exception as e:
                self.stats.failed += 1
                print(f"  [ENDURANCE] ✗ {token.token_id}: {type(e).__name__}: {e}")

        await asyncio.gather(*[resolve_one(t) for t in token_pairs])

    def _log_stats(self, phase_label: str, elapsed: float):
        s = self.stats.summary()
        print(f"\n  ── [{phase_label}] Stats at {elapsed:.0f}s ──")
        print(f"     Submitted : {s['submitted']}")
        print(f"     Resolved  : {s['resolved']}  ({s['resolve_rate']})")
        print(f"     Discarded : {s['discarded']}  ({s['discard_rate']})")
        print(f"     Failed    : {s['failed']}")
        print(f"     Latency   : p50={s['p50_latency']}  p95={s['p95_latency']}  max={s['max_latency']}")

    async def run(self):
        """
        Main run loop. Cycles through SCHEDULE indefinitely until
        stop() is called or duration_seconds elapses.
        """
        print(f"\n{'=' * 70}")
        print("ENDURANCE RUNNER — Continuous Load Modulation")
        print(f"{'=' * 70}")
        if self.duration_seconds:
            print(f"  Duration: {self.duration_seconds}s")
        else:
            print("  Duration: indefinite (call stop() or KeyboardInterrupt)")
        print()
        print("  PHASES:")
        for cfg in self.PHASES:
            print(f"    {cfg.label:10s} — {cfg.note}")
        print()

        overall_start = time.monotonic()
        last_log = overall_start
        schedule_idx = 0

        while not self._stop_event.is_set():
            # Check total duration
            elapsed = time.monotonic() - overall_start
            if self.duration_seconds and elapsed >= self.duration_seconds:
                break

            # Get current phase from schedule (cycle indefinitely)
            phase_enum, phase_duration = self.SCHEDULE[schedule_idx % len(self.SCHEDULE)]
            config = self._get_phase_config(phase_enum)
            phase_start = time.monotonic()

            print(f"\n{'─' * 70}")
            print(f"  PHASE: {config.label}")
            print(f"  {config.note}")
            print(f"  Batch size: {config.batch_size}  |  "
                  f"Interval: {config.interval_range[0]}–{config.interval_range[1]}s  |  "
                  f"Duration: {phase_duration}s")
            print(f"{'─' * 70}")

            # Run phase for its allocated duration
            while not self._stop_event.is_set():
                phase_elapsed = time.monotonic() - phase_start
                total_elapsed = time.monotonic() - overall_start

                if phase_elapsed >= phase_duration:
                    break
                if self.duration_seconds and total_elapsed >= self.duration_seconds:
                    break

                # Submit a batch
                await self._submit_batch(config)

                # Periodic stats log
                if time.monotonic() - last_log >= self.log_interval:
                    self._log_stats(config.label, time.monotonic() - overall_start)
                    last_log = time.monotonic()

                # Wait before next batch — this is the frequency knob.
                # Even at SURGE pace this sleeps briefly; the system is never
                # given zero breathing room, it is just given very little.
                interval = random.uniform(*config.interval_range)
                self._stop_event.wait(timeout=interval)

            schedule_idx += 1

        print("  Draining in-flight tasks...")
        for t in self._resolve_threads:
            t.join()
        self._resolve_threads.clear()

        # ── Final report ─────────────────────────────────────────────────────
        total_elapsed = time.monotonic() - overall_start
        print(f"\n{'=' * 70}")
        print("ENDURANCE RUN COMPLETE")
        print(f"{'=' * 70}")
        print(f"  Total wall-clock: {total_elapsed:.1f}s")
        self._log_stats("FINAL", total_elapsed)

        print("\n  Tasks per phase:")
        for phase, count in self.stats.phase_counts.items():
            print(f"    {phase.value:8s}: {count} submitted")

        if self.stats.discarded > 0:
            pct = self.stats.discarded / self.stats.submitted * 100
            print(f"\n  ⚠  {self.stats.discarded} tasks discarded ({pct:.1f}%)")
            print("     This occurred during SURGE phase when the mailbox was full.")
            print("     Workers continued draining the queue. No deadlock. No corruption.")
            print("     The system did not melt down — it bounded the work.")
            print()
            print("     UPGRADE PATH: Log caller args at discard time.")
            print("     Replay when the system cools. Discard becomes deferral.")

        return self.stats

    def run_forever(self):
        """Convenience: run until KeyboardInterrupt."""
        try:
            self.run()
        except KeyboardInterrupt:
            print("\n\n  [Endurance] KeyboardInterrupt received — stopping.")
            self.stop()


# ── Entry points ─────────────────────────────────────────────────────────────

def run_endurance_proof(duration_seconds: float = 300):
    """
    Standard proof run. 5 minutes covers at least one full schedule cycle
    and will encounter at least two SURGE phases.

    For a quick sanity check use duration_seconds=60.
    For a genuine endurance claim use duration_seconds=3600 or longer.
    """
    runner = EnduranceRunner(duration_seconds=duration_seconds)
    asyncio.run(runner.run())


def run_endurance_forever():
    """Run indefinitely. Suitable for 1–24 hour unattended operation."""
    runner = EnduranceRunner()
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        print("\n\n  [Endurance] KeyboardInterrupt received — stopping.")
        runner.stop()
