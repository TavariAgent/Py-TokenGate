import time
import random
import threading
import queue

from ..operations_coordinator import get_global_coordinator
from ..token_system import task_token_guard

get_global_coordinator()


# ==========================================
# 1. LIGHT: Sum of Squares
# ==========================================
def light_sum_squares(n: int) -> int:
    return sum(i * i for i in range(n))


@task_token_guard(operation_type='cpu_light', tags={'weight': 'light'})
def light_sum_squares_gated(n: int) -> int:
    return sum(i * i for i in range(n))


# ==========================================
# 2. MEDIUM: Prime Counter
# ==========================================
def medium_count_primes(n: int) -> int:
    def is_prime(num):
        if num < 2: return False
        for i in range(2, int(num ** 0.5) + 1):
            if num % i == 0: return False
        return True

    return sum(1 for i in range(n) if is_prime(i))


@task_token_guard(operation_type='cpu_medium', tags={'weight': 'medium'})
def medium_count_primes_gated(n: int) -> int:
    def is_prime(num):
        if num < 2: return False
        for i in range(2, int(num ** 0.5) + 1):
            if num % i == 0: return False
        return True

    return sum(1 for i in range(n) if is_prime(i))


# ==========================================
# 3. HEAVY: Monte Carlo Pi Estimation
# ==========================================
def heavy_estimate_pi(iterations: int) -> float:
    inside_circle = 0
    for _ in range(iterations):
        x, y = random.random(), random.random()
        if x * x + y * y <= 1.0:
            inside_circle += 1
    return (inside_circle / iterations) * 4


@task_token_guard(operation_type='cpu_heavy', tags={'weight': 'heavy'})
def heavy_estimate_pi_gated(iterations: int) -> float:
    inside_circle = 0
    for _ in range(iterations):
        x, y = random.random(), random.random()
        if x * x + y * y <= 1.0:
            inside_circle += 1
    return (inside_circle / iterations) * 4


# ==========================================
# SYNCHRONOUS BATCH MANAGER
# ==========================================

class BatchManager:
    def __init__(self, max_active_batches=2):
        self.queue = queue.Queue()
        self.max_active_batches = max_active_batches
        self.results = {}

        # Callbacks for WebSocket integration
        self.on_progress = None
        self.on_complete = None

    def submit_batch(self, batch_name: str, func, arg, count: int):
        """Synchronously submits a batch to the thread-safe queue."""
        print(f"Queued batch: {batch_name} ({count} operations)")
        self.queue.put((batch_name, func, arg, count))

    def _process_single_batch(self, batch_name, func, arg, count):
        """Processes a single batch and tracks intervals."""
        print(f"--> Starting active batch: {batch_name}")
        start_time = time.perf_counter()

        completed = 0
        for _ in range(count):
            func(arg)
            completed += 1

            # Simple interval counting logic (log every 25% or at completion)
            if completed % max(1, count // 4) == 0 or completed == count:
                if self.on_progress:
                    self.on_progress(batch_name, completed, count)

        duration = time.perf_counter() - start_time
        self.results[batch_name] = duration
        print(f"<-- Finished batch: {batch_name} in {duration:.4f}s")

        if self.on_complete:
            self.on_complete(batch_name, duration)

        return duration

    def _worker(self):
        """Worker thread that pulls from the queue."""
        while True:
            item = self.queue.get()
            if item is None:
                break  # Poison pill to stop worker
            batch_name, func, arg, count = item

            try:
                self._process_single_batch(batch_name, func, arg, count)
            finally:
                self.queue.task_done()

    def run_all_blocking(self):
        """Starts workers and blocks until the queue is empty (for terminal testing)."""
        threads = []
        for _ in range(self.max_active_batches):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            threads.append(t)

        self.queue.join()  # Wait for all tasks in the queue to finish

    def start_background_workers(self):
        """Starts workers in the background and returns immediately (for Flask)."""
        for _ in range(self.max_active_batches):
            threading.Thread(target=self._worker, daemon=True).start()


def main():
    manager = BatchManager(max_active_batches=2)

    # 1. Submit Light Batches
    manager.submit_batch("Light (Ungated)", light_sum_squares, 500, 1000)
    manager.submit_batch("Light (Gated)", light_sum_squares_gated, 500, 1000)

    # 2. Submit Medium Batches
    manager.submit_batch("Medium (Ungated)", medium_count_primes, 1000, 100)
    manager.submit_batch("Medium (Gated)", medium_count_primes_gated, 1000, 100)

    # 3. Submit Heavy Batches
    manager.submit_batch("Heavy (Ungated)", heavy_estimate_pi, 10000, 10)
    manager.submit_batch("Heavy (Gated)", heavy_estimate_pi_gated, 10000, 10)

    print("\nProcessing queue...\n")
    manager.run_all_blocking()

    print("\n=== FINAL RESULTS ===")
    for name, duration in manager.results.items():
        print(f"{name.ljust(20)}: {duration:.4f}s")


if __name__ == "__main__":
    main()