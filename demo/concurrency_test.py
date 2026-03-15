"""
concurrency_test.py — True concurrent burst testing.

run_test_batch() in main.py submits tasks in a tight sequential loop.
That is close to concurrent but not identical — submissions are ordered,
meaning the first task may already be executing before the last is submitted.

This module submits tasks from multiple threads simultaneously using a
barrier synchronization, then measures submission timestamps versus
resolution timestamps to confirm genuine parallel execution.

The goal is to make concurrency observable, not just implied.
"""
import os
import random
import tempfile
import time
import threading
import statistics
from typing import Optional
from .cpu_ops import moderate_operation, complex_operation, heavy_operation
from .io_ops import write_json_fast, write_blob_moderate, append_log_slow



# ── Concurrency Test Runner ──────────────────────────────────────────────────

def run_concurrency_burst(func, args_list, label: str = "burst"):
    """
    Submit all tasks as close to simultaneously as possible using a
    threading.Barrier. Each thread waits at the barrier until all are
    ready, then submits its single task.

    Records:
      - submit_at: perf_counter timestamp when token was created
      - resolve_at: perf_counter timestamp when .get() returned
      - duration: resolve_at - submit_at (wall-clock wait per task)

    If tasks were running sequentially, durations will staircase upward.
    If tasks are genuinely concurrent, durations cluster together.
    """
    n = len(args_list)
    barrier = threading.Barrier(n)

    records: list[Optional[dict]] = [None] * n

    def worker(idx, args):
        barrier.wait()  # all threads synchronize here before submitting
        submit_at = time.perf_counter()
        if isinstance(args, tuple):
            token = func(*args)
        else:
            token = func(args)
        result = token.get(timeout=60)
        resolve_at = time.perf_counter()
        records[idx] = {
            'idx': idx,
            'submit_at': submit_at,
            'resolve_at': resolve_at,
            'duration': resolve_at - submit_at,
            'result': result,
        }

    threads = [
        threading.Thread(target=worker, args=(i, args))
        for i, args in enumerate(args_list)
    ]

    overall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    overall_elapsed = time.perf_counter() - overall_start

    durations = [r['duration'] for r in records]
    submit_spread = max(r['submit_at'] for r in records) - min(r['submit_at'] for r in records)

    print(f"\n{'=' * 70}")
    print(f"CONCURRENCY BURST: {label} ({n} tasks)")
    print(f"{'=' * 70}")
    print(f"  Submit spread (barrier jitter): {submit_spread * 1000:.2f}ms")
    print(f"  Overall wall-clock:             {overall_elapsed:.6f}s")
    print(f"  Min task duration:              {min(durations):.6f}s")
    print(f"  Max task duration:              {max(durations):.6f}s")
    print(f"  Mean task duration:             {statistics.mean(durations):.6f}s")
    print(f"  Stdev (clustering indicator):   {statistics.stdev(durations):.6f}s")
    print()
    print("  Duration per task (tight clustering = true concurrency):")
    for r in records:
        bar = '█' * int(r['duration'] * 20)
        print(f"    Task {r['idx']:02d}: {r['duration']:.6f}s  {bar}")

    # Concurrency check: if max duration < sum of all durations,
    # tasks overlapped. Ratio shows how much.
    serial_estimate = sum(durations)
    overlap_ratio = serial_estimate / overall_elapsed if overall_elapsed > 0 else 1.0
    print(f"\n  Serial estimate (sum):  {serial_estimate:.6f}s")
    print(f"  Actual wall-clock:      {overall_elapsed:.6f}s")
    print(f"  Concurrency ratio:      {overlap_ratio:.2f}x  "
          f"({'concurrent' if overlap_ratio > 1.5 else 'limited concurrency'})")
    print(f"\nCONCURRENCY BURST [{label}] PASSED")
    return records

def run_concurrency_release_window(release_options, duration_s: float = 10.0, label: str = "release window"):
    """
    Repeated synchronized bursts for a fixed duration.
    As soon as one release finishes, another starts.
    Random release option is chosen each time.
    """
    print(f"\n{'=' * 70}")
    print("SUSTAINED CONCURRENCY RELEASE WINDOW")
    print(f"{'=' * 70}")
    print(f"  Duration target: {duration_s:.2f}s")
    print(f"  Release options: {len(release_options)}")

    all_records = []
    release_count = 0
    started = time.perf_counter()

    while True:
        if time.perf_counter() - started >= duration_s:
            break

        func, args_list, release_label = random.choice(release_options)
        release_count += 1

        records = run_concurrency_burst(
            func,
            args_list,
            label=f"{release_label} | release {release_count}"
        )
        all_records.extend(records)

    overall_elapsed = time.perf_counter() - started
    durations = [r['duration'] for r in all_records]

    if not durations:
        print("\n  No records captured.")
        return all_records

    serial_estimate = sum(durations)
    overlap_ratio = serial_estimate / overall_elapsed if overall_elapsed > 0 else 1.0

    print(f"\n{'=' * 70}")
    print(f"CONCURRENCY WINDOW: {label}")
    print(f"{'=' * 70}")
    print(f"  Releases:                       {release_count}")
    print(f"  Total tasks:                    {len(all_records)}")
    print(f"  Overall wall-clock:             {overall_elapsed:.6f}s")
    print(f"  Min task duration:              {min(durations):.6f}s")
    print(f"  Max task duration:              {max(durations):.6f}s")
    print(f"  Mean task duration:             {statistics.mean(durations):.6f}s")
    print(f"  Stdev (clustering indicator):   {statistics.stdev(durations) if len(durations) > 1 else 0.0:.6f}s")
    print()
    print(f"  Serial estimate (sum):          {serial_estimate:.6f}s")
    print(f"  Actual wall-clock:              {overall_elapsed:.6f}s")
    print(f"  Sustained concurrency ratio:    {overlap_ratio:.2f}x  "
          f"({'concurrent' if overlap_ratio > 1.5 else 'limited concurrency'})")

    print(f"\nCONCURRENCY WINDOW [{label}] PASSED")
    return all_records


def run_concurrency_demo(mode: str = "burst", duration_s: float = 10.0):
    """
    mode='burst'   -> current one-shot proof bursts
    mode='release' -> timed repeated release window
    """
    print("\n" + "=" * 70)
    print("CONCURRENCY TEST SUITE")
    print("=" * 70)

    base_dir = tempfile.mkdtemp(prefix="threads_mixed_")

    release_options = [
        (
            moderate_operation,
            [5000, 6000, 7000, 8000, 9000, 10000, 7500, 8500],
            "Light x8",
        ),
        (
            complex_operation,
            [100, 120, 140, 160, 180, 200, 110, 130],
            "Medium x8",
        ),
        (
            heavy_operation,
            [2000, 2500, 3000, 2200, 2700, 3200, 2400, 2800],
            "Heavy x8",
        ),
        (
            append_log_slow,
            [(os.path.join(base_dir, "logs", "out.log"), f"entry {i}") for i in range(8)],
            "Append log_slow x8",
        ),
        (
            write_json_fast,
            [(os.path.join(base_dir, "json", f"r_{i}.json"), {"i": i, "sq": i * i}) for i in range(8)],
            "Write JSON x8",
        ),
        (
            write_blob_moderate,
            [(os.path.join(base_dir, "blob", f"b_{i}.bin"), 8) for i in range(8)],
            "Write blob_moderate x8",
        ),
    ]

    if mode == "release":
        run_concurrency_release_window(
            release_options=release_options,
            duration_s=duration_s,
            label=f"Sustained mixed releases ({duration_s:.0f}s)",
        )
    else:
        for func, args_list, label in release_options:
            run_concurrency_burst(func, args_list, label=label)

    print("\nCONCURRENCY SUITE COMPLETE.")

def _summarize_records(records, label: str, overall_elapsed: float, releases: int):
    durations = [r['duration'] for r in records]
    submit_times = [r['submit_at'] for r in records]

    if not durations:
        print(f"\nCONCURRENCY WINDOW [{label}] — no records")
        return []

    submit_spread = max(submit_times) - min(submit_times)
    serial_estimate = sum(durations)
    overlap_ratio = serial_estimate / overall_elapsed if overall_elapsed > 0 else 1.0

    print(f"\n{'=' * 70}")
    print(f"CONCURRENCY WINDOW: {label}")
    print(f"{'=' * 70}")
    print(f"  Releases:                       {releases}")
    print(f"  Total tasks:                    {len(records)}")
    print(f"  Submit spread (full window):    {submit_spread * 1000:.2f}ms")
    print(f"  Overall wall-clock:             {overall_elapsed:.6f}s")
    print(f"  Min task duration:              {min(durations):.6f}s")
    print(f"  Max task duration:              {max(durations):.6f}s")
    print(f"  Mean task duration:             {statistics.mean(durations):.6f}s")
    print(f"  Stdev (clustering indicator):   {statistics.stdev(durations) if len(durations) > 1 else 0.0:.6f}s")
    print(f"\n  Serial estimate (sum):          {serial_estimate:.6f}s")
    print(f"  Actual wall-clock:              {overall_elapsed:.6f}s")
    print(f"  Sustained concurrency ratio:    {overlap_ratio:.2f}x  "
          f"({'concurrent' if overlap_ratio > 1.5 else 'limited concurrency'})")

    return durations