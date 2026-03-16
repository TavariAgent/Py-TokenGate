# TokenGate/demo/gui_pressure_demo.py

import math
import os
import random
import time
from pathlib import Path

from ..token_system import task_token_guard
from ..operations_coordinator import get_global_coordinator

# Get coordinator (GUI already started it)
coordinator = get_global_coordinator()

@task_token_guard(operation_type='cpu_light', tags={'weight': 'light'})
def cpu_light(size: int):
    total = 0
    for i in range(size):
        total += (i * i) % 97
    return {
        "kind": "cpu_light",
        "size": size,
        "result": total,
    }


@task_token_guard(operation_type='cpu_medium', tags={'weight': "medium"})
def cpu_medium(size: int):
    data = [i * 2 for i in range(size)]
    filtered = [x for x in data if x % 3 == 0]
    return {
        "kind": "cpu_medium",
        "size": size,
        "result": sum(filtered),
    }


@task_token_guard(operation_type='cpu_heavy', tags={'weight': 'heavy'})
def cpu_heavy(size: int):
    total = 0.0
    for i in range(1, size):
        total += math.sqrt(i) * math.sin(i % 360)
    return {
        "kind": "cpu_heavy",
        "size": size,
        "result": total,
    }


@task_token_guard(
    operation_type='append_log',
    tags={'weight': 'heavy', 'storage_speed': 'MODERATE'},
)
def append_log(path: str, message: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    return {
        "kind": "append_log",
        "path": path,
        "chars": len(message),
    }


@task_token_guard(
    operation_type='mixed_op',
    tags={"weight": "medium", "storage_speed": "MODERATE"},
)
def mixed_op(size: int, path: str, message: str):
    total = 0
    for i in range(size):
        total += (i * 7) % 19

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{message} total={total}\n")

    return {
        "kind": "mixed_op",
        "size": size,
        "path": path,
        "result": total,
    }


def submit_batch(batch_size: int, log_path: str, phase_name: str, wave_index: int) -> int:
    submitted = 0

    for i in range(batch_size):
        choice = random.random()

        try:
            if choice < 0.30:
                cpu_light(random.randint(5_000, 20_000))
            elif choice < 0.60:
                cpu_medium(random.randint(4_000, 12_000))
            elif choice < 0.80:
                cpu_heavy(random.randint(8_000, 20_000))
            elif choice < 0.90:
                append_log(
                    log_path,
                    f"[{phase_name}] wave={wave_index} task={i} ts={time.time():.3f}",
                )
            else:
                mixed_op(
                    random.randint(3_000, 10_000),
                    log_path,
                    f"[{phase_name}] mixed wave={wave_index} task={i} ts={time.time():.3f}",
                )

            submitted += 1

        except Exception as e:
            print(f"[DEMO][SUBMIT-ERROR] phase={phase_name} wave={wave_index} task={i}: {e}")

    return submitted


def run_phase(name: str, duration_s: float, batch_size: int, interval_s: float, log_path: str) -> int:
    print(f"[DEMO][PHASE] {name} duration={duration_s}s batch={batch_size} interval={interval_s}s")
    phase_start = time.time()
    wave_index = 0
    total_submitted = 0

    while (time.time() - phase_start) < duration_s:
        wave_index += 1
        submitted = submit_batch(batch_size, log_path, name, wave_index)
        total_submitted += submitted

        print(
            f"[DEMO][WAVE] phase={name} wave={wave_index} "
            f"submitted={submitted} elapsed={time.time() - phase_start:.2f}s"
        )

        time.sleep(interval_s)

    print(f"[DEMO][PHASE-END] {name} total_submitted={total_submitted}")
    return total_submitted


def short_main():
    print("[DEMO] Starting short GUI burst demo...")

    try:
        out_dir = Path("demo_output")
        out_dir.mkdir(exist_ok=True)
        log_path = str(out_dir / "gui_pressure_short.log")

        run_phase("burst", 12, 35, 0.15, log_path)
    finally:
        print("[DEMO] Demo stopped.")


def main():
    print("[DEMO] Starting GUI pressure demo...")


    out_dir = Path("demo_output")
    out_dir.mkdir(exist_ok=True)
    log_path = str(out_dir / "gui_pressure_demo.log")

    started = time.time()
    total_submitted = 0


    total_submitted += run_phase("baseline", 15, 25, 0.75, log_path)
    total_submitted += run_phase("steady", 25, 35, 0.35, log_path)
    total_submitted += run_phase("burst", 20, 50, 0.10, log_path)
    total_submitted += run_phase("recovery", 20, 10, 1.00, log_path)

    elapsed = time.time() - started
    print(
        f"[DEMO] Complete. submitted={total_submitted} "
        f"elapsed={elapsed:.2f}s log={log_path}"
    )

    print()
    print("=" * 70)
    print("FINAL STATISTICS")
    print("=" * 70)

    stats = coordinator.get_stats()

    print(f"\nToken Pool:")
    print(f"  Total created: {stats['token_pool']['total_created']}")
    print(f"  Total admitted: {stats['token_pool']['total_admitted']}")

    print(f"\nAdmission Gate:")
    print(f"  Total admitted: {stats['admission_gate']['total_admitted']}")
    print(f"  Mode: {stats['admission_gate']['mode']}")

    print(f"\nWorker Queue:")
    print(f"  Total executed: {stats['worker_queue']['total_executed']}")
    print(f"  Total failed: {stats['worker_queue']['total_failed']}")

    if 'affinity' in stats and stats['affinity']:
        print(f"\nCore Affinity:")
        print(f"  Total routed: {stats['affinity']['total_routed']}")

    # Guard House report
    print()
    print("=" * 70)
    print("GUARD HOUSE REPORT")
    print("=" * 70)
    coordinator.print_guard_house_dashboard()

if __name__ == "__main__":
    main()