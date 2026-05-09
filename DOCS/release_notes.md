# TokenGate — Release Notes

## Hash Conductor & Sticky Token Registry

This release introduces two systems that work together to anchor token execution  
to stable core domains for the full lifetime of a call chain. The result is  
deterministic routing, zero cross-domain data races, and measurably better  
behaviour under saturated load conditions.  

---

## What Changed

### StickyTokenRegistry

Tokens carrying the same `(operation_type, args)` key are now pinned to the  
core that first receives them. Any later token arriving with the same key is  
redirected to that core automatically. The pin releases when the token  
completes, freeing the next submission to route normally.  

A `sticky_anchor` tag can be added to any decorator to give the sticky key an  
explicit name, independent of operation type.  

```python
@task_token_guard(
    operation_type="my_op",
    tags={"weight": "medium", "sticky_anchor": "my_domain"},
)
def my_operation(n: int) -> int:
    ...
```

### HashConductor

Lead tokens — those decorated with `external_calls` — generate a SHA-256 seed  
from their token ID and call list. That seed is pinned to a core domain. Any  
token spawned during the lead's execution inherits the seed and is routed to the  
same core automatically. The domain releases when the lead and all of its  
children have completed.  

```python
@task_token_guard(
    operation_type="lead_op",
    tags={"weight": "medium", "external_calls": ["child_op"]},
)
def lead_operation(n: int) -> list:
    return [child_op(n + i) for i in range(4)]
```

No changes are required at call sites. Domain anchoring is fully automatic once  
`external_calls` is declared.  

### State Machine Cleanup

`conductor.on_complete()` is now wired into `transition_state()` directly. Every  
terminal state — `COMPLETED`, `FAILED`, `KILLED`, `TIMEOUT` — decrements the  
pending count. Killed tokens are reported as completed for observability clarity.  
Domains cannot leak regardless of how a token ends.  

---

## How Routing Works — Layer by Layer

Understanding the full path a token takes from call to completion.  

**Layer 1 — Core Pinning Workers in Their Domains:**   
Workers are fixed to a single core domain at startup and never move. `HEAVY`  
tokens belong to Core 1. `MEDIUM` tokens belong to Core 2 and above. `LIGHT`  
tokens belong to Core 3 and above. Workers sit in their domain and reach for  
the nearest valid token. The queue is what moves — it forms itself into the  
correct shape around the workers, routing tokens into position so each worker  
always reaches the right one.  

**Layer 2 — Token Creation & Metadata:**  
`task_token_guard` intercepts the decorated call before execution. A `TaskToken`  
is created carrying the function, arguments, operation type, and routing tags.  
Complexity scoring runs once and is cached on the wrapper. If an active conductor  
seed is present in the current executor thread, it is stamped onto the token's  
metadata here, before any event loop crossing occurs.  

**Layer 3 — Weight Classification & Position Assignment:**  
The token is classified as `HEAVY`, `MEDIUM`, or `LIGHT` from its tags or  
operation type name. A staggered global position is calculated from the per-core  
position counter, respecting the active worker pattern for that core. The  
candidate core and local worker index are derived from this position.  

**Layer 4 — Sticky Token Enforcement:**  
For standard tokens, `sticky_registry.mark()` is called with the resolved  
`sticky_anchor` or operation type as the key. If a marker already exists for  
this key, the token is redirected to the pinned core. If not, the candidate core  
is pinned and returned. This prevents concurrent tokens with matching keys from  
splitting across core domains.  

**Layer 5 — Seed Generation:**  
For lead tokens carrying `external_calls`, a SHA-256 digest is computed from the  
token ID concatenated with a frozen representation of the call list. The full  
64-character hex digest is used. Collision probability at any realistic token  
volume is negligible. The seed is stored on the token's metadata tags.  

**Layer 6 — Core Resolution:**  
`_put_routing_block` makes the final routing decision. Lead tokens with  
`external_calls` go to `conductor.charge()`. Tokens carrying a  
`conductor_seed` tag go to `conductor.register_child()`. All other tokens  
follow the normal sticky registry path. The returned `core_id` is written to  
the token's `sticky_core` tag and used for all subsequent mailbox placements.  

**Layer 7 — Charge Lead:**  
`conductor.charge()` generates the seed, pins it to the candidate core via the  
sticky registry, and opens the pending count at 1 for the lead itself. The seed  
and core mapping are stored in the conductor's internal registry for the lifetime  
of the chain.

**Layer 8 — Pre-Register Children:**  
When the lead function runs in the executor thread and spawns child tokens,  
`task_token_guard` stamps the active seed onto each child at creation time and  
calls `conductor.pre_register()` to increment the pending count immediately.  
This increment happens before the child crosses to the event loop, ensuring the  
domain stays alive even if the lead completes before the children reach `put()`.  

**Layer 9 — Register Children:**  
When the child arrives at `put()`, `register_child()` reads the seed from the  
token tag and returns the pinned `core_id`. Position assignment still runs —  
but now it runs with a `core_id` that is already established. `assign_position_for_token`  
uses that known `core_id` to derive `local_i`, selecting the correct local worker  
within the domain. The staggered counter is not bypassed; it is given the right  
context to work from. This keeps domain membership valid as worker counts change  
live.

**Layer 10 — Domain Grouping:**  
With the target `core_id` confirmed, the least-loaded active local worker within  
that core is selected. The token is placed into that worker's mailbox. All tokens  
under the same seed land in the same core's mailbox domain, keeping related work  
physically co-located.

**Layer 11 — Execution:**  
The worker loop dequeues the token and calls `_execute_token_wrapped`. Before the  
function runs, `conductor.activate()` sets the conductor seed into a thread-local  
on the executor thread. If the function spawns further child tokens, they  
automatically inherit the seed through the same creation-time stamping mechanism  
in `task_token_guard`.

**Layer 12 — Finalization: Cleanup & Metadata:**  
When the token transitions to any terminal state (`COMPLETED`, `FAILED`,  
`KILLED`, `TIMEOUT`), `transition_state()` calls `conductor.on_complete()`.  
The pending count decrements. When it reaches zero — meaning the lead and every  
child have finished — the seed is removed from the conductor registry and the  
sticky pin is released. Execution timing, core assignment, and complexity score  
are written to the execution record.  

---

## Test Coverage Added

**Cache Storm Test** (`demo/cache_storm.py`)  
Submits anchor tokens to pin keys to cores, then fires concurrent bursts of  
tokens with identical `(op_type, args)` keys targeting different cores. Verifies  
the sticky registry redirects all of them to the pinned core with zero misses.  

**Hash Conductor Test** (`demo/hash_conductor_test.py`)  
Submits lead tokens that spawn children during execution. Verifies all tokens  
in each chain land on the same core, carry matching seeds, and that the conductor  
snapshot is empty after resolution. Also checks seed uniqueness across  
independent concurrent leads.  

---

## Benchmark

Endurance run across 15 doubling waves. 131,068 tokens total.  

```
Wave   Tokens    OK      Fail    Time      Tok/s     Lat(ms)   Conc    Overlap
1      4         4       0       0.003s    1386.2    0.721     1.00×   1.44×
2      8         8       0       0.003s    2391.2    0.418     1.72×   2.48×
3      16        16      0       0.006s    2744.8    0.364     1.98×   4.82×
4      32        32      0       0.011s    2812.7    0.356     2.03×   11.32×
5      64        64      0       0.022s    2880.0    0.347     2.08×   22.01×
6      128       128     0       0.044s    2907.6    0.344     2.10×   29.78×
7      256       256     0       0.090s    2846.8    0.351     2.05×   37.98×
8      512       512     0       0.182s    2811.5    0.356     2.03×   41.81×
9      1024      1024    0       0.364s    2813.9    0.355     2.03×   44.18×
10     2048      2048    0       0.775s    2644.3    0.378     1.91×   44.86×
11     4096      4096    0       1.454s    2816.3    0.355     2.03×   38.34×
12     8192      8192    0       2.905s    2819.9    0.355     2.03×   32.64×
13     16384     16384   0       5.925s    2765.0    0.362     1.99×   27.92×
14     32768     32768   0       12.102s   2707.7    0.369     1.95×   24.96×
15     65536     65536   0       23.494s   2789.5    0.358     2.01×   24.21×

TOTAL  131,068   131,068  0      89.091s
Avg latency : 0.386 ms/token
Peak overlap: 44.86×
```

Zero failures. Latency holds within 0.04ms from wave 3 through wave 15.  
Peak overlap of 44.86× achieved at wave 10 with flat latency — the system  
reached saturation and held position rather than degrading.  

---

## Files Added

```
threads/sticky_token.py
threads/hash_conductor.py
threads/demo/cache_storm_ops.py
threads/demo/cache_storm.py
threads/demo/hash_conductor_ops.py
threads/demo/hash_conductor_test.py
```

## Files Modified

```
threads/core_pinned_staggered_queue.py   routing, executor wrapper, cleanup
threads/token_system.py                  seed stamping, state machine hook
threads/tg_print.py                      sticky + conductor channels registered
```
