# TokenGate — Optimization Pass Release Notes

## Summary

This pass targets the token submission hot path — the sequence of operations
between a caller invoking a decorated function and the token landing in a
worker mailbox. No routing contracts, no execution semantics, and no public
API signatures were changed. Also added a runtime gaurd for tokens inserted
into the coordinator. All improvements are opt-in or transparent.

---

## Benchmark Comparison

| Metric                        | Before       | After         | Delta      |
|-------------------------------|--------------|---------------|------------|
| Total wall time (131k tokens) | 83.506s      | 42.337s       | −49%       |
| Active execution time         | ~83s         | 4.673s        | −94%       |
| Avg latency / token           | 0.351ms      | 0.086ms       | −75%       |
| Peak concurrency ratio        | 3.15×        | 49.22×        | +15.6×     |
| Peak overlap ratio            | 46.09×       | 47.39×        | maintained |
| Peak single-wave throughput   | ~3,368 tok/s | ~64,754 tok/s | ~19×       |
| Sustained throughput          | ~1,569 tok/s | ~28,046 tok/s | ~18×       |

The concurrency jump from 3× to 49× indicates the submission path was the
real ceiling. Workers were idle-waiting on routing overhead. Once that
overhead was removed the thread pool expressed its actual capacity.

---

## Throughput Reporting — How to Read the Numbers

The benchmark reports four throughput figures. Each measures something
distinct and they should not be compared directly without understanding
what each one counts.

**Important:** All mean calculations are derived from accumulated token
totals and elapsed time totals across waves — they are **not** arithmetic
averages of independent per-wave unit rates. Reading them as per-wave
averages will make them appear inconsistent with wall time. They are not.

| Metric                  | Formula                               | What it measures                                                                                                                                           |
|-------------------------|---------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Sustained**           | `total_tokens / Σ wave_elapsed`       | Volume-weighted reality. Dominated by the largest waves which carry the most tokens. The honest number for full-workload throughput.                       |
| **Peak**                | `max(tokens_i / elapsed_i)`           | Best single-wave rate. Reflects the sweet-spot batch size where parallelism is fullest and scheduling overhead is smallest.                                |
| **Token-weighted mean** | `Σ(rate_i × tokens_i) / total_tokens` | Each token votes equally on the average rate. Sits between sustained and peak — useful for understanding where the system spends most of its token-budget. |
| **Arithmetic mean**     | `Σ rate_i / N`                        | Each wave votes equally. Small fast waves inflate this significantly. Included for completeness but the least representative of real workload behaviour.   |

**Why sustained and wall time appear inconsistent:**

Wall time includes 15 × 0.05s = 0.75s of deliberate inter-wave sleep plus
event loop scheduling gaps and print overhead. Active time is the raw sum of
`asyncio.gather` spans only. The `tok/s` figure in each wave row and in the
summary is always based on active time. Wall time is reported separately so
the two are never conflated.

**Example from the final benchmark run:**

```
Waves 13–15:  114,688 tokens / 4.266s  =  ~26,900 tok/s  ← 87% of all volume
Waves  1–12:   16,380 tokens / 0.407s  =  ~40,200 tok/s  ← 13% of all volume
Combined:      131,068 tokens / 4.673s  =  ~28,046 tok/s  ← sustained (correct)
```

The sustained rate is pulled toward the large-wave rate because large waves
dominate the token count. This is the correct and expected result.

---

## Changes

### 1. `unhashable_checker.py` — O(1) Type Dispatch

**Problem:** `make_hashable` walked a 23-layer `isinstance` chain on every
unhashable value, including common exact types like `dict`, `list`, and
`np.ndarray`.

**Change:** Added `_DISPATCH: dict` populated once at module load by
`_build_dispatch()`. At call time, a single `_DISPATCH.get(type(obj))`
lookup short-circuits to the correct handler for registered exact types.
Subclass misses fall through to the existing `isinstance` chain — no
coverage regression.

Registered at load time: `dict`, `list`, `set`, `bytearray`, `memoryview`,
`slice`, `array.array`, `deque`, `OrderedDict`, `defaultdict`, `Counter`,
`ChainMap`, and optionally `np.ndarray`, `pd.DataFrame`, `pd.Series`,
`pd.MultiIndex`, `pd.Index`, `pd.Categorical`, `torch.Tensor`, `cp.ndarray`,
`PIL.Image`.

**Fast path fix:** The `hash()` fast path now catches `(TypeError,
RuntimeError)` instead of `TypeError` only. Non-scalar `torch.Tensor` raises
`RuntimeError` from `hash()` and previously slipped through to the tensor
handler at layer 6. This closes that gap consistently with `is_hashable`.

---

### 2. `unhashable_checker.py` — `HashPolicy` Enum

**Problem:** All tokens paid the full `make_hashable` pipeline cost at
submission time regardless of whether their operation used sticky routing
or conductor domain anchoring.

**Change:** Added `HashPolicy` enum with four levels:

| Value      | Behaviour                                                                                    |
|------------|----------------------------------------------------------------------------------------------|
| `NONE`     | No arg hashing. `route_args` is always `()`. Free routing only.                              |
| `FAST`     | Builtins and stdlib only (layers 1–3). Unknown types get identity routing `(type_name, id)`. |
| `STANDARD` | Full `make_hashable` pipeline. Default — unchanged behaviour.                                |
| `FULL`     | Same as `STANDARD`. Reserved for explicit subclass-fallthrough intent.                       |

Set per-operation via decorator tag:

```python
@task_token_guard(
    operation_type="conductor_lead",
    tags={"weight": "medium",
          "hash_policy": HashPolicy.FAST,
          "digest_policy": DigestPolicy.FAST,
          "external_calls": ["conductor_child"]},
)
def my_function(...): ...
```

Default remains `STANDARD`. No existing code changes behaviour without opt-in.

Also added `fast_make_hashable()` — the reduced pipeline used by
`HashPolicy.FAST`. Covers layers 1–3, falls back to `(type_name, id)` for
unrecognised types.

---

### 3. `unhashable_checker.py` — `DigestPolicy` Enum

**Problem:** Conductor seed generation was hardwired to SHA-256 full 64-char
hex on every lead token submission regardless of volume or lifetime
requirements.

**Change:** Added `DigestPolicy` enum with four levels:

| Value     | Algorithm         | Output   | Collision space                          |
|-----------|-------------------|----------|------------------------------------------|
| `FULL`    | SHA-256           | 64 chars | 256-bit. Default, unchanged.             |
| `SHORT`   | SHA-256 truncated | 16 chars | 64-bit. Safe at any realistic volume.    |
| `FAST`    | BLAKE2s (8-byte)  | 16 chars | 64-bit. Lower compute cost than SHA-256. |
| `MINIMAL` | SHA-256 truncated | 8 chars  | 32-bit. Low volume only.                 |

**Collision semantics for `MINIMAL`:** Collisions merge two logical domain
chains into a shared mailbox cluster. No data corruption occurs — the
least-loaded mechanism compensates. Under saturation, heavy tasks may fall
back from their primary core, shifting load distribution and inertly reducing
variance. The effect is benign at low-to-mid token volume with short-lived
leads: collisions reduce domain variance rather than causing failures, but
they can force heavy tasks off their primary core under sustained call chains,
inertly reducing performance at the affinity boundary.

Set per-operation via decorator tag:

```python
@task_token_guard(
    operation_type="my_op",
    tags={"digest_policy": DigestPolicy.FAST}
)
def my_function(...): ...
```

---

### 4. `hash_conductor.py` — Policy-Aware `generate_seed` and `charge`

**Change:** `generate_seed` now accepts a `DigestPolicy` parameter and
branches to the appropriate algorithm. `charge` reads the `digest_policy`
tag from the token's metadata, resolves string values to the enum with a
fallback-to-FULL warning, and passes the resolved policy to `generate_seed`.

The `tg_print` dispatch line in `charge` now includes `digest={policy.value}`
for observability during mixed-policy runs.

`snapshot()` `seed[:12]` truncation is safe for all four policies — Python's
slice never raises on out-of-bounds, so an 8-char `MINIMAL` seed returns
itself.

---

### 5. `sticky_token.py` — `freeze()` Empty-Args Guard

**Problem:** `_make_key` called `freeze(args)` on every `mark()` and
`unmark()`, including the majority path where `args = ()`. `freeze(())` is
always `()` — the recursive call was redundant on every conductor
`on_complete` → `unmark(seed, ())` call.

**Change:**
```python
return op_name, freeze(args) if args else ()
```

Zero overhead on the empty-args path which is now the dominant path through
the conductor release cycle.

---

### 6. `core_pinned_staggered_queue.py` — Loop and Boolean Simplification

| Location                               | Change                                                                                                                        |
|----------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `_choose_local_worker_least_loaded`    | Manual `best_local/best_len` loop → `min(range(active), key=lambda i: mailbox.qsize())`                                       |
| `choose_worker_for_core`               | Same pattern → `min(range(active), key=lambda i: worker_queue_sizes[base+i])`                                                 |
| `assign_worker_positions`              | `append` loop → list comprehension                                                                                            |
| `classify_token_weight` tag path       | `if/elif/else` string compare → `_WEIGHT_MAP.get(weight_str, TaskWeight.MEDIUM)` class-level dict                             |
| `_put_routing_block` else-branch       | `if external_calls or has_sticky:` → `if has_sticky:` — `external_calls` already ruled out by outer `if`, dead branch removed |
| `_put_routing_block` FAST `route_args` | Unreachable `external_calls` condition on sticky-only path removed                                                            |

---

### 7. `core_affinity_queue.py` — Loop and Boolean Simplification

| Location                | Change                                                                                                |
|-------------------------|-------------------------------------------------------------------------------------------------------|
| `_build_preferences`    | Verbose `if/else` with redundant fallback prints → inline ternary + single preference dict literal    |
| `get_affinity_report`   | Build loop with `if total > 0` branch → dict comprehension with walrus `:=`                           |
| `print_affinity_report` | `if core_key in report:` + index → walrus `if (stats := report.get(...)):` — eliminates double lookup |

---

### 8. `demo/max_concurrency_test.py` — Timer and Reporting Overhaul

**Problem:** Per-wave timing used `time.perf_counter()` floats which
accumulate rounding error on sub-millisecond waves. Total elapsed swallowed
15 × 0.5s = 7.5s of inter-wave sleep, making overall `tok/s` appear ~60%
lower than the true active-time rate.

**Results:**  

```terminaloutput
======================================================================================
  RESULTS SUMMARY
======================================================================================
  Wave   Tokens   OK    Fail       Time      Tok/s   Lat(ms)    Conc   Overlap   ΣTask(ms)
  ----------------------------------------------------------------------------------------
  1      4        4     0        2.968ms     1347.6    0.742ms   1.00×     1.53×       4.55ms
  2      8        8     0        1.054ms     7592.3    0.132ms   5.63×     5.82×       6.13ms
  3      16       16    0        1.086ms    14738.4    0.068ms  10.94×    12.59×      13.67ms
  4      32       32    0        2.227ms    14370.4    0.070ms  10.66×    15.92×      35.45ms
  5      64       64    0        1.210ms    52870.7    0.019ms  39.23×    30.52×      36.94ms
  6      128      128   0        2.222ms    57595.4    0.017ms  42.74×    41.00×      91.12ms
  7      256      256   0        4.548ms    56293.4    0.018ms  41.77×    42.24×     192.10ms
  8      512      512   0       14.849ms    34479.5    0.029ms  25.59×    24.28×     360.62ms
  9      1024     1024  0       15.814ms    64754.0    0.015ms  48.05×    46.56×     736.24ms
  10     2048     2048  0       32.664ms    62698.8    0.016ms  46.53×    46.48×    1518.38ms
  11     4096     4096  0       91.186ms    44919.2    0.022ms  33.33×    34.42×    3138.82ms
  12     8192     8192  0      237.752ms    34456.1    0.029ms  25.57×    26.43×    6282.80ms
  13     16384    16384 0      593.702ms    27596.3    0.036ms  20.48×    21.52×   12777.36ms
  14     32768    32768 0     1186.575ms    27615.6    0.036ms  20.49×    22.38×   26554.21ms
  15     65536    65536 0     2485.401ms    26368.4    0.038ms  19.57×    23.58×   58612.42ms
  ----------------------------------------------------------------------------------------
  TOTAL  131068   131068 0       active 4.673s  wall 42.337s <- 
                                        ^
  49% reduction from 83.506s total wall time, but active time is the real story here.

  Overall throughput (active time) : 28,046.4 tok/s
  Avg latency across waves         : 0.086 ms/token
  Peak concurrency ratio           : 48.05×
  Peak overlap ratio               : 46.56×

  Active time  = Σ wave elapsed only  (excludes 0.75s inter-wave sleep)
  Wall time    = full orchestrator span including sleep and scheduling

  Overlap ratio = Σ(individual task times) / wave elapsed time
  Values above 1× indicate true parallel execution.
  Values approaching N = N tasks running simultaneously.
======================================================================================
```

**Changes:**

- Per-wave timing switched to `time.perf_counter_ns()` — integer nanoseconds,
  no float accumulation error.
- `tok_per_sec` computed as `(target × 1_000_000_000) / elapsed_ns` — integer
  arithmetic throughout.
- `total_active_ns` accumulates wave-only elapsed time. Inter-wave sleep is
  explicitly excluded from all throughput calculations.
- Summary reports `active time` and `wall time` as separate lines — never
  conflated.
- Inter-wave sleep reduced from 0.5s to 0.05s — `asyncio.gather` guarantees
  completion before sleep runs; 0.5s was dead time.
- Four throughput lines added to summary. See **Throughput Reporting** section
  above for full definitions.

---

## Files Changed

| File                             | Type                                                                       |
|----------------------------------|----------------------------------------------------------------------------|
| `unhashable_checker.py`          | Extended — `HashPolicy`, `DigestPolicy`, `_DISPATCH`, `fast_make_hashable` |
| `hash_conductor.py`              | Modified — policy-aware seed generation                                    |
| `sticky_token.py`                | Modified — empty-args freeze guard                                         |
| `core_pinned_staggered_queue.py` | Modified — loop simplification, dead branch removal                        |
| `core_affinity_queue.py`         | Modified — loop and boolean simplification                                 |
| `demo/max_concurrency_test.py`   | Modified — NS timing, active/wall split, four throughput metrics           |

---

## Constraints and Notes

- `HashPolicy.NONE` removes all arg-based sticky anchoring. Any operation
  tagged `NONE` that also carries `external_calls` will lose content-keyed
  domain routing and fall back to candidate-core only. Explicit opt-in.
- `DigestPolicy` values must not be mixed mid-run on the same operation type.
  Seeds are stamped at `charge` time and inherited by children directly —
  mixing is structurally prevented but worth noting for configuration
  management.
- `_DISPATCH` only short-circuits on exact type matches. Subclasses fall
  through to the `isinstance` chain. No coverage regression.
- All policy defaults are unchanged. No existing decorated function changes
  behaviour without an explicit tag opt-in.
- Throughput figures in the benchmark are based on **accumulated totals across
  all waves**, not averages of independent per-wave unit rates. Wall time
  includes inter-wave sleep and scheduling gaps and will always appear higher
  than active time would suggest. This is expected and correct.