"""
runner.py — Interactive REPL for the Threads proof suite.

Internal test tool. Run directly:
    python -m threads.demo.runner
    python runner.py  (from demo directory)

Type a number or keyword at the prompt.
The endurance runner blocks until it finishes or you press Ctrl+C.
"""
import asyncio
import os
import tempfile

from .cpu_ops import (
    trivial_operation, simple_operation, string_operation,
    moderate_operation, prime_operation, complex_operation,
    heavy_operation, fibonacci_operation,
)
from .io_ops import write_json_fast, append_log_slow, write_blob_moderate
from .series_ops import run_series_saturation_demo
from .chain_ops import run_chain_demo, run_parallel_chains_demo
from .concurrency_test import run_concurrency_demo
from .endurance import EnduranceRunner

from ..operations_coordinator import OperationsCoordinator


# ── Shared state ──────────────────────────────────────────────────────────────

_coordinator: OperationsCoordinator = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_coordinator() -> bool:
    if _coordinator is None:
        print("  [!] Coordinator is not running. Type 'start' or '1' first.")
        return False
    return True


def _run_test_batch(name, func, args_list, expected_count):
    print(f"\n  Submitting {len(args_list)} tasks → {name}")
    tokens = [func(*a) if isinstance(a, tuple) else func(a) for a in args_list]
    results, failed = [], 0
    for i, token in enumerate(tokens):
        try:
            results.append(token.get(timeout=30))
        except Exception as e:
            failed += 1
            print(f"    ✗ Task {i} failed: {e}")
    print(f"  {len(results)}/{len(args_list)} resolved  |  {failed} failed")
    if results:
        print(f"  Sample: {results[:4]}")
    assert len(results) == expected_count, f"Expected {expected_count}, got {len(results)}"
    print(f"  ✓ {name}")
    return results


# ── Command handlers ──────────────────────────────────────────────────────────

def cmd_start():
    global _coordinator
    if _coordinator is not None:
        print("  Coordinator already running.")
        return
    _coordinator = OperationsCoordinator()
    _coordinator.start()
    print("  ✓ Coordinator started.")


def cmd_stop():
    global _coordinator
    if _coordinator is None:
        print("  Coordinator is not running.")
        return
    _coordinator.stop()
    _coordinator = None
    print("  ✓ Coordinator stopped.")


def cmd_status():
    state = "running" if _coordinator else "stopped"
    print(f"\n  Coordinator: {state}")


def cmd_cpu():
    if not _require_coordinator(): return
    print("\n── CPU-BOUND PROOF ──────────────────────────────────────────────────")
    _run_test_batch("Trivial",   trivial_operation,   list(range(20)),20)
    _run_test_batch("Simple",    simple_operation,    [100, 200, 300, 150, 250] * 4,20)
    _run_test_batch("String",    string_operation,    [10, 20, 30, 15, 25] * 3,15)
    _run_test_batch("Moderate",  moderate_operation,  [500, 1000, 1500, 750, 1250] * 3,15)
    _run_test_batch("Prime",     prime_operation,      [97,101,103,107,109,113,127,131,137,139, 100,102,104,106,108],15)
    _run_test_batch("Complex",   complex_operation,   [10, 15, 20, 12, 18] * 2,                 10)
    _run_test_batch("Heavy",     heavy_operation,     [100, 150, 200, 120, 180] * 2,10)
    _run_test_batch("Fibonacci", fibonacci_operation, [100,200,300,150,250,180,220,280],8)
    print("\n  ✓ CPU proof complete.")


def cmd_io():
    if not _require_coordinator(): return
    print("\n── I/O-BOUND PROOF ──────────────────────────────────────────────────")
    base_dir = tempfile.mkdtemp(prefix="threads_io_")
    print(f"  Output directory: {base_dir}")
    json_jobs = [(os.path.join(base_dir, "json", f"r_{i}.json"), {"i": i, "sq": i * i}) for i in range(10)]
    log_jobs = [(os.path.join(base_dir, "logs", "out.log"), f"entry {i}") for i in range(10)]
    blob_jobs = [(os.path.join(base_dir, "blob", f"b_{i}.bin"), 8) for i in range(6)]
    _run_test_batch("JSON writes", write_json_fast,     json_jobs, 10)
    _run_test_batch("Log appends", append_log_slow,     log_jobs,  10)
    _run_test_batch("Blob writes", write_blob_moderate, blob_jobs,  6)
    print("\n  ✓ I/O proof complete.")


def cmd_series():
    if not _require_coordinator(): return
    run_series_saturation_demo()


def cmd_chain():
    if not _require_coordinator(): return
    run_chain_demo(chain_count=5)
    run_parallel_chains_demo(chain_count=4)


def cmd_concurrency():
    if not _require_coordinator(): return

    mode = input("  Mode: burst or release (default burst): ").strip().lower()
    if not mode:
        mode = "burst"

    if mode not in ("burst", "release"):
        print("  [!] Invalid mode. Use 'burst' or 'release'.")
        return

    duration = 10.0
    if mode == "release":
        raw = input("  Duration in seconds (default 10): ").strip()
        duration = float(raw) if raw else 10.0

    run_concurrency_demo(mode=mode, duration_s=duration)

def cmd_endurance():
    if not _require_coordinator(): return
    raw = input("  Duration in seconds (blank = run until Ctrl+C): ").strip()
    duration = float(raw) if raw else None
    runner = EnduranceRunner(duration_seconds=duration, log_interval=20.0)
    print("  Running endurance suite. Press Ctrl+C to stop early.\n")
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        print("\n  Interrupted — stopping endurance runner.")
        runner.stop()


def cmd_all():
    if not _require_coordinator(): return
    cmd_cpu()
    cmd_io()
    cmd_series()
    cmd_chain()
    cmd_concurrency()
    print("\n  ✓ Full proof suite complete.")


def cmd_mixed():
    if not _require_coordinator(): return
    from .orchestrator import run_mixed_orchestrator
    raw = input("  Duration in seconds (default 60): ").strip()
    duration = float(raw) if raw else 60.0
    run_mixed_orchestrator(duration_seconds=duration)


def cmd_help():
    print("""
  ── Threads Demo REPL ────────────────────────────────────────────────────

   1  start        Start the OperationsCoordinator
   2  stop         Stop the coordinator
   3  status       Show coordinator state

   4  cpu          CPU-bound scheduling proof
   5  io           I/O-bound scheduling proof
   6  series       Backpressure / pool saturation demo
   7  chain        Explicit call chain + parallel chains demo
   8  concurrency  Concurrent burst with overlap measurement
   9  endurance    Sustained load modulator (blocks, Ctrl+C to stop early)
  10  all          Run all proofs sequentially (excludes endurance)
  11  mixed        Operate a mixed set

      help / ?     Show this message
      exit / quit  Stop coordinator and exit

  ─────────────────────────────────────────────────────────────────────────
""")


# ── Dispatch ──────────────────────────────────────────────────────────────────

COMMANDS = {
    '1':  cmd_start,       'start':       cmd_start,
    '2':  cmd_stop,        'stop':        cmd_stop,
    '3':  cmd_status,      'status':      cmd_status,
    '4':  cmd_cpu,         'cpu':         cmd_cpu,
    '5':  cmd_io,          'io':          cmd_io,
    '6':  cmd_series,      'series':      cmd_series,
    '7':  cmd_chain,       'chain':       cmd_chain,
    '8':  cmd_concurrency, 'concurrency': cmd_concurrency,
    '9':  cmd_endurance,   'endurance':   cmd_endurance,
    '10': cmd_all,         'all':         cmd_all,
    '11': cmd_mixed,       'mixed':       cmd_mixed,
    '?':  cmd_help,        'help':        cmd_help,

}

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║              Threads — Proof Suite REPL                          ║
║  Type a number or keyword.  'help' to list commands.             ║
║  Start here: 'start'  or  '1'                                    ║
╚══════════════════════════════════════════════════════════════════╝
"""


# ── REPL loop ─────────────────────────────────────────────────────────────────

def repl():
    print(BANNER)
    while True:
        try:
            raw = input("threads> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        low = raw.lower()

        if low in ('exit', 'quit'):
            break

        handler = COMMANDS.get(low)
        if handler:
            try:
                handler()
            except AssertionError as e:
                print(f"  [FAIL] {e}")
            except Exception as e:
                print(f"  [ERROR] {type(e).__name__}: {e}")
        else:
            print(f"  Unknown command: '{raw}'.  Type 'help' or '?' for options.")

    if _coordinator:
        print("  Stopping coordinator...")
        cmd_stop()
    print("  Goodbye.")


if __name__ == "__main__":
    repl()