"""
leibniz_pi.py
─────────────────────────────────────────────────────────────────────────────
Leibniz PI approximation built around:
  - __slots__ Term and Chunk instances (proliferating, not expanding)
  - Alphabetic label system  A..Z → AA..AZ → BA..ZZ → AAA... (symbol table)
  - Per-chunk inline algebra dictionary (grows alongside numeric work)
  - Chunked partial sums unified at the end
  - Configurable rounds — push ROUNDS at the bottom to stress your hardware
─────────────────────────────────────────────────────────────────────────────
"""
import string
import time

from ..operations_coordinator import OperationsCoordinator
from ..token_system import task_token_guard

LETTERS = string.ascii_uppercase

# ── Rounds ─────────────────────────────────────────────────────────────────
# Each tuple is (total_terms, chunk_size).
# Add more rows or bump the numbers to probe your hardware limits.

ROUNDS = [
    (100, 10),
    (1_000, 20),
    (10_000, 50),
    (100_000, 100),
    (1_000_000, 500),
]


# ── Label  (pure function — stateless, fully thread-safe) ──────────────────
# Derives the alphabetic label for any index directly, no shared state.
# index 0→A, 1→B ... 25→Z, 26→AA, 27→AB ... 701→ZZ, 702→AAA ...
# Any thread can call this independently with any index at any time.
def index_to_label(i: int) -> str:
    label, n = "", i
    while True:
        label = LETTERS[n % 26] + label
        n = n // 26 - 1
        if n < 0:
            break
    return label


# ── Term ───────────────────────────────────────────────────────────────────
# One Leibniz term:  (-1)^n / (2n+1)
# __slots__ — contained table, proliferates across instances, never expands.

class Term:
    __slots__ = ["index", "sign", "denominator", "value", "label"]

    def __init__(self, index: int, label: str):
        self.index = index
        self.sign = 1 if index % 2 == 0 else -1
        self.denominator = 2 * index + 1
        self.value = self.sign / self.denominator
        self.label = label

    def algebra(self) -> str:
        s = "+" if self.sign > 0 else "-"
        return f"({s}1/{self.denominator})"


# ── Chunk ──────────────────────────────────────────────────────────────────
# A pocket of Terms. Owns its partial sum and algebra expression.
# Each Chunk is fully self-contained — no dependencies between siblings.

class Chunk:
    __slots__ = ["label", "terms", "partial_sum", "algebra_expr"]

    def __init__(self, label: str, terms: list):
        self.label = label
        self.terms = terms
        self.partial_sum = sum(t.value for t in terms)
        self.algebra_expr = "  ".join(t.algebra() for t in terms)


# ══════════════════════════════════════════════════════════════════════════
#  Pipeline — three independent stages, each threadable at its own seam
# ══════════════════════════════════════════════════════════════════════════
@task_token_guard(operation_type="generate_terms", tags={"weight": "medium", "sticky_anchor": "gen_token"})
def generate_terms(total_terms: int) -> list:
    """
    Stage 1 — Term generation.
    Produces all Term instances across [0, total_terms).
    Labels are derived via index_to_label(i) — pure, no shared state.
    Thread seam: split range into sub-ranges, each worker calls
    index_to_label(i) independently and owns its slice of Term instances.
    """
    return [Term(i, index_to_label(i)) for i in range(total_terms)]


@task_token_guard(operation_type="build_chunks", tags={"weight": "heavy", "sticky_anchor": "build_token"})
def build_chunks(terms: list, chunk_size: int) -> list:
    """
    Stage 2 — Chunking and partial sum computation.
    Slices terms into Chunk instances; each Chunk computes its own
    partial_sum and algebra_expr at construction.
    Thread seam: each Chunk(label, slice) call is fully independent —
    workers can construct and compute any subset of chunks simultaneously.
    Chunk labels derived from chunk index via index_to_label, not a generator.
    """
    return [
        Chunk(index_to_label(chunk_idx), terms[i: i + chunk_size])
        for chunk_idx, i in enumerate(range(0, len(terms), chunk_size))
    ]


@task_token_guard(operation_type="unify_chunks", tags={"weight": "medium", "sticky_anchor": "unify_token"})
def unify_chunks(chunks: list) -> float:
    """
    Stage 3 — Unification.
    Sums all partial sums and scales by 4 to produce the PI approximation.
    Sequential by nature — depends on all chunks being complete.
    Thread seam: if chunks arrive from workers out of order, collect into
    a results list then sum here; order of addition doesn't affect the total.
    """
    return 4.0 * sum(c.partial_sum for c in chunks)


# ── Orchestrator & Alias ──────────────────────────────────────────────────────

def run_alias():
    coordinator = OperationsCoordinator()
    coordinator.start()
    try:
        for round_terms, round_chunk_size in ROUNDS:
            run(round_terms, round_chunk_size)
    except KeyboardInterrupt:
        print("  Interrupted by user.")
    finally:
        coordinator.stop()


def run(total_terms: int, chunk_size: int = 50, show_chunks: int = 4) -> float:
    bar = "─" * 66

    print(f"\n{bar}")
    print(f"  ROUND  │  terms = {total_terms:>10,}  │  chunk size = {chunk_size:>6,}")
    print(bar)

    t_start = time.perf_counter()

    terms = generate_terms(total_terms)
    chunks = build_chunks(terms, chunk_size)
    pi_approx = unify_chunks(chunks)
    pi_value = float(pi_approx)

    t_end = time.perf_counter()
    elapsed_ms = (t_end - t_start) * 1_000

    algebra_table = {c.label: c.algebra_expr for c in chunks}

    print(f"\n  Algebra table  (first {show_chunks} of {len(chunks)} chunks)")
    print(f"  {'Label':<5}  Expression")
    print(f"  {'─' * 5}  {'─' * 55}")
    for label, expr in list(algebra_table.items())[:show_chunks]:
        display = expr if len(expr) <= 55 else expr[:52] + "..."
        print(f"  {label:<5}  {display}")

    print()
    print(f"  π  ≈  {pi_value:.15f}")
    print(f"  Δ     {abs(pi_value - 3.141592653589793236):.4e}   (error vs math.pi)")
    print(f"  ⏱     {elapsed_ms:>10.3f} ms")
    print(f"\n{bar}\n")

    return float(pi_approx)


if __name__ == "__main__":
    run_alias()
