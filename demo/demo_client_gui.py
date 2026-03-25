import tkinter as tk
from tkinter import scrolledtext
import threading
import time
import random

from ..operations_coordinator import get_global_coordinator
from ..token_system import task_token_guard

# Initialize the TokenGate event bus
get_global_coordinator()


# ==========================================
# GATED MATH FUNCTIONS
# ==========================================
@task_token_guard(operation_type='cpu_light', tags={'weight': 'light'})
def light_sum_squares_gated(n: int) -> int:
    return sum(i * i for i in range(n))


@task_token_guard(operation_type='cpu_medium', tags={'weight': 'medium'})
def medium_count_primes_gated(n: int) -> int:
    def is_prime(num):
        if num < 2: return False
        for i in range(2, int(num ** 0.5) + 1):
            if num % i == 0: return False
        return True

    return sum(1 for i in range(n) if is_prime(i))


@task_token_guard(operation_type='cpu_heavy', tags={'weight': 'heavy'})
def heavy_estimate_pi_gated(iterations: int) -> float:
    inside_circle = 0
    for _ in range(iterations):
        x, y = random.random(), random.random()
        if x * x + y * y <= 1.0:
            inside_circle += 1
    return (inside_circle / iterations) * 4


# ==========================================
# NATIVE GUI CLIENT
# ==========================================
class TokenGateClientApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TokenGate Native Client App")
        self.root.geometry("500x400")
        self.root.configure(bg="#1e1e1e")

        # Title
        tk.Label(root, text="Native Client Operations", fg="white", bg="#1e1e1e", font=("Arial", 14, "bold")).pack(pady=10)

        # Buttons
        button_frame = tk.Frame(root, bg="#1e1e1e")
        button_frame.pack(pady=5)

        tk.Button(button_frame, text="Run Light Batch", bg="#007acc", fg="white", width=15,
                  command=lambda: self.trigger_batch("Light", light_sum_squares_gated, 500, 1000)
                  ).grid(row=0, column=0, padx=5)

        tk.Button(button_frame, text="Run Medium Batch", bg="#007acc", fg="white", width=15,
                  command=lambda: self.trigger_batch("Medium", medium_count_primes_gated, 1000, 100)
                  ).grid(row=0, column=1, padx=5)

        tk.Button(button_frame, text="Run Heavy Batch", bg="#007acc", fg="white", width=15,
                  command=lambda: self.trigger_batch("Heavy", heavy_estimate_pi_gated, 10000, 10)
                  ).grid(row=0, column=2, padx=5)

        # Log Window
        self.log_area = scrolledtext.ScrolledText(root, height=15, bg="#252526", fg="#dcdcaa", font=("Consolas", 10))
        self.log_area.pack(padx=20, pady=10, fill="both", expand=True)

        self.log("Client Ready. Connected to TokenGate Coordinator.")

    def log(self, message):
        """Thread-safe logging to the Tkinter UI"""

        def update_ui():
            self.log_area.insert(tk.END, f"[System] {message}\n")
            self.log_area.see(tk.END)

        self.root.after(0, update_ui)

    def process_batch(self, name, func, arg, count):
        """Runs the math batch in a background thread so the GUI doesn't freeze"""
        self.log(f"--> Starting {name} ({count} tasks)")
        start_time = time.perf_counter()

        for _ in range(count):
            func(arg)  # Wakes up TokenGate decorators!

        duration = time.perf_counter() - start_time
        self.log(f"<-- Finished {name} in {duration:.4f}s")

    def trigger_batch(self, name, func, arg, count):
        """Spawns a thread for the batch execution"""
        threading.Thread(target=self.process_batch, args=(name, func, arg, count), daemon=True).start()


def main():
    root = tk.Tk()
    app = TokenGateClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()