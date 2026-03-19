#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
 TokenGate — PI Pyramid Stress Test
 Leibniz Series  ·  Pyramid Chain  ·  1,000,000+ Tokens
───────────────────────────────────────────────────────────────────────────────
 Formula
 ───────
 Gregory-Leibniz series (intentionally the slowest converging PI series):
   π/4 = 1 - 1/3 + 1/5 - 1/7 + 1/9 - ...
   term(n) = (-1)^n / (2n + 1)
   π = 4 × Σ term(n)  for n = 0 → N

 This series is famous for being maximally slow. ~10M terms for 7 correct
 digits. The mathematical inefficiency is the point — each token does real
 arithmetic but the system must process enormous volume to make progress.
 The stress test proves throughput, not mathematical elegance.

 Pyramid chain structure
 ───────────────────────
 Layer 0  →  1,000,000  pi_term tokens         (light)   one term each
 Layer 1  →      1,000  pi_chunk tokens        (light)   sum 1,000 terms
 Layer 2  →         10  pi_partial tokens      (medium)  sum 100 chunks
 Layer 3  →          1  pi_converge token      (heavy)   ×4, full report
 ─────────────────────────────────────────────────────────────────────
 Total    →  1,001,011  tokens

 Alongside PI, the run also submits:
   - parity checks on term indices        (light)
   - light series accumulations           (light)
   - chain operations (seed→filter→acc)   (light/medium)

 SMT note
 ────────
 With SMT enabled the topology detector will show 2× logical cores.
 Workers per core scales automatically. Expect significantly higher
 sustained throughput than the non-SMT baseline.

 Precision
 ─────────
 Python's decimal module is used throughout with a high-precision context.
 The final report shows every digit resolved and flags where Leibniz
 diverges from the known value of π.

 Requires: Python 3.10+  ·  TokenGate installed
═══════════════════════════════════════════════════════════════════════════════
"""

import time
import math
from decimal import Decimal, getcontext
from typing import List

from ..operations_coordinator import OperationsCoordinator
from ..token_system import task_token_guard

# ── PRECISION CONFIG ──────────────────────────────────────────────────────────

# High precision context for all Decimal arithmetic.
# 50 digits gives room to show exactly where Leibniz runs out of steam.
DECIMAL_PRECISION = 50
getcontext().prec = DECIMAL_PRECISION

# ── PYRAMID CONFIG ────────────────────────────────────────────────────────────

TOTAL_TERMS   = 1_000_000   # Layer 0 token count
CHUNK_SIZE    = 1_000       # Terms per chunk  (Layer 1)
PARTIAL_SIZE  = 100         # Chunks per partial (Layer 2)
# Layer 3 = one final convergence token

# ── COMPANION TEST CONFIG ─────────────────────────────────────────────────────

PARITY_COUNT  = 10_000      # Parity check tokens
SERIES_COUNT  = 5_000       # Light series tokens
CHAIN_COUNT   = 1_000       # Chain operation sets (5 tokens per chain)


# ── LAYER 0: TERM TOKENS ──────────────────────────────────────────────────────

@task_token_guard(operation_type='pi_term', tags={'weight': 'light'})
def compute_pi_term(n: int) -> str:
    """
    Compute a single Leibniz term: (-1)^n / (2n + 1)
    Returns as string to preserve Decimal precision across token boundary.
    Light weight — 1,000,000 of these fire simultaneously.
    """
    getcontext().prec = DECIMAL_PRECISION
    sign = Decimal(-1) ** n
    term = sign / Decimal(2 * n + 1)
    return str(term)


# ── LAYER 1: CHUNK TOKENS ─────────────────────────────────────────────────────

@task_token_guard(operation_type='pi_chunk', tags={'weight': 'light'})
def sum_chunk(term_strings: List[str]) -> str:
    """
    Sum a batch of Leibniz terms.
    Receives resolved term strings from Layer 0 tokens.
    Light weight — 1,000 of these, each summing 1,000 terms.
    """
    getcontext().prec = DECIMAL_PRECISION
    total = sum(Decimal(t) for t in term_strings)
    return str(total)


# ── LAYER 2: PARTIAL TOKENS ───────────────────────────────────────────────────

@task_token_guard(operation_type='pi_partial', tags={'weight': 'medium'})
def sum_partial(chunk_strings: List[str]) -> str:
    """
    Sum a batch of chunk sums.
    Receives resolved chunk strings from Layer 1 tokens.
    Medium weight — 10 of these, each summing 100 chunks.
    """
    getcontext().prec = DECIMAL_PRECISION
    total = sum(Decimal(c) for c in chunk_strings)
    return str(total)


# ── LAYER 3: CONVERGENCE TOKEN ────────────────────────────────────────────────

@task_token_guard(operation_type='pi_converge', tags={'weight': 'heavy'})
def converge_pi(partial_strings: List[str], wall_time: float, total_tokens: int) -> dict:
    """
    Final accumulation: sum all partials × 4 = π estimate.
    Produces the full decimal report with digit-by-digit comparison
    against known π value.
    Heavy weight — one token, the capstone of the pyramid.
    """
    getcontext().prec = DECIMAL_PRECISION

    leibniz_pi = sum(Decimal(p) for p in partial_strings) * 4

    # Known π to 50 digits for comparison
    known_pi = Decimal(
        "3.14159265358979323846264338327950288419716939937510"
    )

    # Find first diverging digit
    lei_str   = str(leibniz_pi)
    known_str = str(known_pi)
    correct_digits = 0
    for a, b in zip(lei_str, known_str):
        if a == b:
            correct_digits += 1
        else:
            break

    return {
        "pi_estimate":     str(leibniz_pi),
        "pi_known":        str(known_pi),
        "correct_digits":  correct_digits,
        "terms_used":      TOTAL_TERMS,
        "wall_time_s":     round(wall_time, 3),
        "total_tokens":    total_tokens,
        "tokens_per_sec":  round(total_tokens / wall_time, 1) if wall_time > 0 else 0,
    }


# ── COMPANION: PARITY CHECKS ──────────────────────────────────────────────────

@task_token_guard(operation_type='pi_parity', tags={'weight': 'light'})
def parity_check(n: int) -> dict:
    """
    Check parity of a Leibniz term index.
    Fires alongside the pyramid — adds light load pressure.
    """
    term_sign = "positive" if n % 2 == 0 else "negative"
    return {"n": n, "sign": term_sign, "value_hint": 1.0 / (2 * n + 1)}


# ── COMPANION: LIGHT SERIES ───────────────────────────────────────────────────

@task_token_guard(operation_type='pi_series_light', tags={'weight': 'light'})
def light_series(start: int, length: int) -> float:
    """
    Simple accumulation over a short integer range.
    Keeps the light worker pool saturated between chunk resolutions.
    """
    return sum(i * 0.001 for i in range(start, start + length))


# ── COMPANION: CHAIN OPERATIONS ───────────────────────────────────────────────

@task_token_guard(operation_type='chain_seed_pi', tags={'weight': 'light'})
def chain_seed(value: int) -> int:
    return value * 3 + 7

@task_token_guard(operation_type='chain_filter_pi', tags={'weight': 'light'})
def chain_filter(value: int) -> int:
    return value // 2 if value % 2 == 0 else value * 2 + 1

@task_token_guard(operation_type='chain_accumulate_pi', tags={'weight': 'medium'})
def chain_accumulate(value: int) -> int:
    return sum(i * value for i in range(1, 26))

@task_token_guard(operation_type='chain_reduce_pi', tags={'weight': 'light'})
def chain_reduce(value: int) -> int:
    return value % 9973

@task_token_guard(operation_type='chain_finalize_pi', tags={'weight': 'light'})
def chain_finalize(value: int) -> dict:
    return {"result": value, "parity": "even" if value % 2 == 0 else "odd"}


# ── DISPLAY ───────────────────────────────────────────────────────────────────

def _header(title: str, W: int = 70) -> None:
    print()
    print("╔" + "═" * W + "╗")
    print("║  " + title.ljust(W - 2) + "║")
    print("╚" + "═" * W + "╝")


def _print_report(report: dict) -> None:
    W = 70
    print()
    print("╔" + "═" * W + "╗")
    print("║  " + "PI PYRAMID — FINAL REPORT".ljust(W - 2) + "║")
    print("╠" + "═" * W + "╣")
    print("║  " + f"Formula      : Gregory-Leibniz  (slowest converging series)".ljust(W - 2) + "║")
    print("║  " + f"Terms used   : {report['terms_used']:,}".ljust(W - 2) + "║")
    print("║  " + f"Total tokens : {report['total_tokens']:,}".ljust(W - 2) + "║")
    print("║  " + f"Wall time    : {report['wall_time_s']}s".ljust(W - 2) + "║")
    print("║  " + f"Throughput   : {report['tokens_per_sec']:,} tokens/sec".ljust(W - 2) + "║")
    print("╠" + "═" * W + "╣")
    print("║  " + "RESULT".ljust(W - 2) + "║")
    print("║  " + f"Estimated π  : {report['pi_estimate']}".ljust(W - 2) + "║")
    print("║  " + f"Known π      : {report['pi_known']}".ljust(W - 2) + "║")
    print("║  " + f"Correct digits: {report['correct_digits']}  (Leibniz needs ~10M terms per digit)".ljust(W - 2) + "║")
    print("╠" + "═" * W + "╣")

    # Digit-by-digit alignment
    est   = report['pi_estimate']
    known = report['pi_known']
    print("║  " + "Digit comparison (^ = correct, x = diverged):".ljust(W - 2) + "║")
    print("║  " + ("  " + est[:40]).ljust(W - 2) + "║")
    markers = ""
    for a, b in zip(est[:40], known[:40]):
        markers += "^" if a == b else "x"
    print("║  " + ("  " + markers).ljust(W - 2) + "║")
    print("╚" + "═" * W + "╝")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _header("TokenGate — PI Pyramid Stress Test  ·  Leibniz  ·  1M+ Tokens")

    print(f"""
  Pyramid layout:
    Layer 0  →  {TOTAL_TERMS:>10,}  pi_term tokens      (light)
    Layer 1  →  {TOTAL_TERMS // CHUNK_SIZE:>10,}  pi_chunk tokens     (light)
    Layer 2  →  {TOTAL_TERMS // CHUNK_SIZE // PARTIAL_SIZE:>10,}  pi_partial tokens   (medium)
    Layer 3  →  {'1':>10}  pi_converge token   (heavy)

  Companion load:
    Parity checks  →  {PARITY_COUNT:>8,}  tokens  (light)
    Light series   →  {SERIES_COUNT:>8,}  tokens  (light)
    Chain sets     →  {CHAIN_COUNT:>8,}  sets × 5 tokens each

  Total approximate token count: {TOTAL_TERMS + TOTAL_TERMS // CHUNK_SIZE + TOTAL_TERMS // CHUNK_SIZE // PARTIAL_SIZE + 1 + PARITY_COUNT + SERIES_COUNT + CHAIN_COUNT * 5:,}
    """)

    coordinator = OperationsCoordinator()
    coordinator.start()

    run_start = time.monotonic()

    try:
        # ── LAYER 0: submit all term tokens ───────────────────────────────────
        _header(f"Layer 0 — Submitting {TOTAL_TERMS:,} pi_term tokens")
        t0 = time.monotonic()
        term_tokens = [compute_pi_term(n) for n in range(TOTAL_TERMS)]
        print(f"  Submitted in {time.monotonic() - t0:.3f}s")

        # ── COMPANION: parity, series, chains ─────────────────────────────────
        _header("Companion — Parity · Series · Chains")
        parity_tokens = [parity_check(n) for n in range(0, PARITY_COUNT * 10, 10)]
        series_tokens = [light_series(i * 100, 50) for i in range(SERIES_COUNT)]
        chain_tokens  = []
        for i in range(CHAIN_COUNT):
            seed = chain_seed(i)
            chain_tokens.append(seed)
        print(f"  Companion tokens submitted: {PARITY_COUNT + SERIES_COUNT + CHAIN_COUNT:,}")

        # ── RESOLVE LAYER 0 ───────────────────────────────────────────────────
        _header(f"Layer 0 — Resolving {TOTAL_TERMS:,} terms...")
        t0 = time.monotonic()
        term_results = [t.get(timeout=300) for t in term_tokens]
        print(f"  Resolved {len(term_results):,} terms in {time.monotonic() - t0:.3f}s")

        # ── LAYER 1: chunk tokens ─────────────────────────────────────────────
        num_chunks = TOTAL_TERMS // CHUNK_SIZE
        _header(f"Layer 1 — Submitting {num_chunks:,} pi_chunk tokens")
        chunk_tokens = []
        for i in range(num_chunks):
            batch = term_results[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
            chunk_tokens.append(sum_chunk(batch))

        t0 = time.monotonic()
        chunk_results = [t.get(timeout=120) for t in chunk_tokens]
        print(f"  Resolved {len(chunk_results):,} chunks in {time.monotonic() - t0:.3f}s")

        # ── LAYER 2: partial tokens ───────────────────────────────────────────
        num_partials = num_chunks // PARTIAL_SIZE
        _header(f"Layer 2 — Submitting {num_partials:,} pi_partial tokens")
        partial_tokens = []
        for i in range(num_partials):
            batch = chunk_results[i * PARTIAL_SIZE : (i + 1) * PARTIAL_SIZE]
            partial_tokens.append(sum_partial(batch))

        t0 = time.monotonic()
        partial_results = [t.get(timeout=60) for t in partial_tokens]
        print(f"  Resolved {len(partial_results):,} partials in {time.monotonic() - t0:.3f}s")

        # ── RESOLVE COMPANIONS ────────────────────────────────────────────────
        _header("Companion — Resolving...")
        _ = [t.get(timeout=120) for t in parity_tokens]
        _ = [t.get(timeout=120) for t in series_tokens]
        _ = [t.get(timeout=120) for t in chain_tokens]
        print("  Companion tokens resolved.")

        # ── LAYER 3: final convergence ────────────────────────────────────────
        total_wall = time.monotonic() - run_start
        total_tok  = (TOTAL_TERMS + num_chunks + num_partials + 1
                      + PARITY_COUNT + SERIES_COUNT + CHAIN_COUNT)

        _header("Layer 3 — Final Convergence (heavy token)")
        converge_token = converge_pi(partial_results, total_wall, total_tok)
        report = converge_token.get(timeout=60)

        _print_report(report)

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
    finally:
        coordinator.stop()
        print("\n  Coordinator stopped.")


if __name__ == "__main__":
    main()