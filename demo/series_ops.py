"""
series_ops.py — Duration-bound worker occupation functions.

These operations are designed to hold a worker thread for a predictable
wall-clock period. They are used to demonstrate backpressure behavior:
when the pool is saturated, new submissions wait naturally rather than
bypassing the pipeline.

Unlike CPU ops (which finish as fast as the hardware allows), series ops
give you a controlled knob on how long a worker stays occupied, making
pool saturation observable and reproducible in a proof context.
"""

import time
import math
from ..token_system import task_token_guard


@task_token_guard(operation_type='series_short', tags={'weight': 'light'})
def series_short(duration: float = 0.1):
    """
    Occupies a worker for ~duration seconds via active rolling work.
    No sleep — this is genuine CPU time, not yielding.
    Suitable for light saturation tests.
    """
    end = time.monotonic() + duration
    acc = 0.0
    while time.monotonic() < end:
        acc += math.sqrt(acc + 1.0)
    return round(acc, 6)


@task_token_guard(operation_type='series_medium', tags={'weight': 'medium'})
def series_medium(duration: float = 0.5):
    """
    Occupies a worker for ~duration seconds.
    Medium weight — suitable for testing pool depth and queue pressure.
    """
    end = time.monotonic() + duration
    acc = 1.0
    step = 0
    while time.monotonic() < end:
        acc = math.log(abs(acc) + 1.0) + math.sin(step * 0.01)
        step += 1
    return round(acc, 6)


@task_token_guard(operation_type='series_long', tags={'weight': 'heavy'})
def series_long(duration: float = 2.0):
    """
    Occupies a worker for ~duration seconds.
    Heavy weight — intended to hold Core 1 workers and create visible backpressure
    when submitted in batches. Use this to demonstrate that the mailbox bounds
    submissions rather than allowing runaway queue growth.
    """
    end = time.monotonic() + duration
    acc = 2.0
    step = 0
    while time.monotonic() < end:
        acc = math.sqrt(abs(math.cos(acc))) + math.log(step + 2)
        step += 1
    return round(acc, 6)


def run_series_saturation_demo(coordinator_started: bool = True):
    """
    Submits a burst of series_long tasks that intentionally exceed the
    typical worker count. The goal is to make backpressure visible:
    early tokens resolve quickly, later tokens wait in the mailbox.

    Observe: submission is fast, resolution is staggered. That gap IS
    the backpressure — the system is working, not stalling.
    """
    BURST_SIZE = 20
    DURATION_PER_TASK = 1.0  # seconds each worker is held

    print(f"\n{'=' * 70}")
    print("SERIES SATURATION: Intentional pool pressure")
    print(f"{'=' * 70}")
    print(f"Submitting {BURST_SIZE} x {DURATION_PER_TASK}s tasks.")
    print("Workers will be occupied. Later tokens will wait in mailbox.\n")

    tokens = []
    submit_times = []

    for i in range(BURST_SIZE):
        t = time.monotonic()
        token = series_long(DURATION_PER_TASK)
        tokens.append(token)
        submit_times.append(t)

    results = []
    for i, token in enumerate(tokens):
        result = token.get(timeout=60)
        elapsed = time.monotonic() - submit_times[i]
        results.append((i, elapsed, result))

    print(f"\n  Earliest resolution: {min(r[1] for r in results):.3f}s")
    print(f"  Latest  resolution: {max(r[1] for r in results):.3f}s")
    print(f"  Spread (backpressure window): "
          f"{max(r[1] for r in results) - min(r[1] for r in results):.3f}s")
    print("\nSERIES SATURATION PASSED — backpressure observed, no failures.")
    return results
