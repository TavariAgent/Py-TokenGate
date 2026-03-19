# An Explanative Guide to Cache Mutations, Heuristics and Mutability/Immutability Using `dataclasses`

> Python's `dataclasses` module is more than a shortcut for writing `__init__`. It's a clean way to reason
> about *how your data behaves* — whether it can change, whether it's safe to cache, and whether it can
> be trusted as a key in a dictionary or a set. This guide walks through all of that with runnable examples.

---

## Table of Contents

1. [What is a Dataclass?](#1-what-is-a-dataclass)
2. [Mutability vs Immutability](#2-mutability-vs-immutability)
3. [Mutable Default Fields — The Trap](#3-mutable-default-fields--the-trap)
4. [Frozen Dataclasses and Hashing](#4-frozen-dataclasses-and-hashing)
5. [Cache Mutations — When Caching Goes Wrong](#5-cache-mutations--when-caching-goes-wrong)
6. [Heuristics and Why Mutability Matters](#6-heuristics-and-why-mutability-matters)
7. [Shallow vs Deep Copy with Dataclasses](#7-shallow-vs-deep-copy-with-dataclasses)
8. [Quick Reference](#8-quick-reference)
9. [Mini-Project — Letter Density Set Predictor](#9-mini-project--letter-density-set-predictor)

---

## 1. What is a Dataclass?

A `dataclass` is a class decorated with `@dataclass` that auto-generates boilerplate methods
like `__init__`, `__repr__`, and `__eq__` based on your field annotations.  

 
The most basic use is just a simple container for data:

```python
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float

p = Point(1.0, 2.0)
print(p)          # Point(x=1.0, y=2.0)
print(p.x)        # 1.0
```

Without `@dataclass` you'd write `__init__`, `__repr__`, and `__eq__` manually.
With it, Python generates them for you based on the annotations you define.

---

## 2. Mutability vs Immutability

By default, a dataclass is **mutable** — you can freely reassign its fields after creation.  

These "object value containers" are mutable, so you can change their state after they've been created.   
This is often useful, but it also means you have to be careful about shared references and caching.

```python
from dataclasses import dataclass

@dataclass
class Config:
    host: str
    port: int

cfg = Config("localhost", 8080)
cfg.port = 9090  # Fine — mutable
print(cfg)       # Config(host='localhost', port=9090)
```

To make a dataclass **immutable**, use `frozen=True`. Any attempt to modify a field
will raise a `FrozenInstanceError` at runtime. (In this case the error is improtant to prevent silent cache mutations —   
see section 5.)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    host: str
    port: int

cfg = Config("localhost", 8080)
cfg.port = 9090  # Raises: FrozenInstanceError: cannot assign to field 'port'
```

**Why does this matter?**

- Mutable objects are flexible but can cause subtle bugs when shared across different parts of your code.
- Immutable objects are safe to share, cache, and use as dictionary keys — because nothing can
  change them under you.

---

## 3. Mutable Default Fields — The Trap

This is one of the most common Python mistakes for developers new to `dataclasses` or even just   
new to Python's function defaults:

```python
from dataclasses import dataclass

# THIS WILL RAISE AN ERROR at class definition time
@dataclass
class BadPlayer:
    name: str
    inventory: list = []  # ValueError: mutable default is not allowed
```

Python raises this error intentionally. If you used a bare `[]` as a default, every instance
would *share the same list*, leading to this:

```python
# Simulating what would happen without the safety check:
class BrokenPlayer:
    def __init__(self, name, inventory=[]):
        self.name = name
        self.inventory = inventory

a = BrokenPlayer("Alice")
b = BrokenPlayer("Bob")

a.inventory.append("sword")
print(b.inventory)  # ['sword'] — Bob got Alice's sword!
```

The correct way with `dataclass` is to use `field(default_factory=...)`:

```python
from dataclasses import dataclass, field

@dataclass
class Player:
    name: str
    inventory: list = field(default_factory=list)

a = Player("Alice")
b = Player("Bob")

a.inventory.append("sword")
print(a.inventory)  # ['sword']
print(b.inventory)  # []  — Bob is unaffected, as expected
```

`default_factory` takes a *callable* (like `list`, `dict`, or a lambda) that gets called
fresh for every new instance.

---

## 4. Frozen Dataclasses and Hashing

When you set `frozen=True`, Python also makes the dataclass **hashable** by generating a
`__hash__` method. This means you can use frozen dataclasses as dictionary keys or set members.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Point:
    x: float
    y: float

# Frozen dataclasses are hashable
visited = set()
visited.add(Point(1.0, 2.0))
visited.add(Point(3.0, 4.0))
visited.add(Point(1.0, 2.0))  # Duplicate — won't be added

print(visited)  # {Point(x=1.0, y=2.0), Point(x=3.0, y=4.0)}

# Works as dict keys too
scores = {Point(0.0, 0.0): 100, Point(1.0, 1.0): 87}
print(scores[Point(0.0, 0.0)])  # 100
```

Trying the same with a *mutable* dataclass fails:

```python
@dataclass
class MutablePoint:
    x: float
    y: float

p = MutablePoint(1.0, 2.0)
s = {p}  # TypeError: unhashable type: 'MutablePoint'
```

Python refuses to hash mutable objects as keys because if the object changed after being
inserted, it would become unreachable in the dictionary — the hash would point to the
wrong bucket.

> **Rule of thumb:** If your dataclass needs to live in a `set` or be a `dict` key, freeze it.

---

## 5. Cache Mutations — When Caching Goes Wrong

Caching is the practice of storing the result of an expensive computation so you don't
repeat it. Mutable dataclasses introduce a subtle danger here: **the cached value can
be mutated from outside the cache**.

### Example: Silent Cache Corruption

```python
from dataclasses import dataclass
from functools import lru_cache

@dataclass
class QueryResult:
    rows: list
    count: int

# Simulated expensive DB call
_cache = {}

def fetch_data(query: str) -> QueryResult:
    if query in _cache:
        print(f"[cache hit] '{query}'")
        return _cache[query]
    
    print(f"[cache miss] Running query: '{query}'")
    result = QueryResult(rows=["row1", "row2"], count=2)
    _cache[query] = result
    return result

# First call — populates cache
r1 = fetch_data("SELECT * FROM users")

# Caller mutates the returned object
r1.rows.append("injected_row")
r1.count = 999

# Second caller gets the corrupted cached result
r2 = fetch_data("SELECT * FROM users")
print(r2.rows)   # ['row1', 'row2', 'injected_row'] — corrupted!
print(r2.count)  # 999 — corrupted!
```

This is a **cache mutation bug**. The cache holds a reference to the mutable object.
When the first caller mutates it, the corruption silently propagates to every future caller.

### Fix 1: Return a copy from the cache

```python
import copy

def fetch_data_safe(query: str) -> QueryResult:
    if query in _cache:
        return copy.deepcopy(_cache[query])  # Return a fresh copy
    result = QueryResult(rows=["row1", "row2"], count=2)
    _cache[query] = result
    return copy.deepcopy(result)  # Store and return copies
```

### Fix 2: Use frozen dataclasses so mutation is impossible

```python
from dataclasses import dataclass
from typing import tuple

@dataclass(frozen=True)
class QueryResult:
    rows: tuple  # tuples are immutable, so safe in frozen dataclass
    count: int

_cache = {}

def fetch_data_frozen(query: str) -> QueryResult:
    if query in _cache:
        return _cache[query]  # Safe to return the same object — it can't be mutated
    result = QueryResult(rows=("row1", "row2"), count=2)
    _cache[query] = result
    return result

r1 = fetch_data_frozen("SELECT * FROM users")
# r1.rows = ("tampered",)  # FrozenInstanceError — mutation blocked at the source
```

> **Key insight:** If your cached object is immutable, you never need to worry about cache
> corruption. Frozen dataclasses with immutable field types (tuples, strings, ints) are
> ideal cache values.

---

## 6. Heuristics and Why Mutability Matters

A **heuristic** is an estimate used to guide a decision — commonly seen in pathfinding
algorithms like A* where you estimate the cost to reach a goal from a given node.

Heuristic data is a great example of *intentionally mutable* state, because the estimates
get refined as you explore. Here the mutability is a feature, not a bug — but it must be
managed carefully.

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Node:
    name: str
    g_cost: float = 0.0        # Actual cost from start to here
    h_cost: float = 0.0        # Heuristic estimate: cost from here to goal
    parent: Optional['Node'] = field(default=None, repr=False) # Typing becomes very important

    @property
    def f_cost(self) -> float:
        """Total estimated cost: actual + heuristic."""
        return self.g_cost + self.h_cost

def heuristic(node: Node, goal: Node) -> float:
    """Simple Manhattan distance heuristic."""
    return abs(hash(node.name) - hash(goal.name)) % 10  # Toy example

# Build a small graph
start = Node("A")
mid   = Node("B")
goal  = Node("C")

# Update heuristic estimates as we explore
mid.g_cost = 3.0
mid.h_cost = heuristic(mid, goal)
mid.parent = start

print(mid)          # Node(name='B', g_cost=3.0, h_cost=..., f_cost=...)
print(mid.f_cost)   # g + h combined
```

Now imagine you've cached heuristic values to avoid recomputing them:

```python
heuristic_cache = {}

def get_heuristic(node: Node, goal: Node) -> float:
    key = (node.name, goal.name)
    if key not in heuristic_cache:
        heuristic_cache[key] = heuristic(node, goal)
    return heuristic_cache[key]
```

This is safe *only* if the heuristic is **pure** (same input always produces same output)
and node identity doesn't change between calls. The moment your Node's name or identity
becomes mutable and you rename a node mid-search, your cache entries become stale.

> **Design principle:** Mutable state is fine during active computation. Freeze or snapshot
> it before caching. Don't cache references to objects that are still being modified.

---

## 7. Shallow vs Deep Copy with Dataclasses

Python's `dataclasses` module provides a `replace()` utility that returns a *shallow copy*
of a frozen or mutable dataclass with specific fields overridden — similar to Rust's
struct update syntax.

```python
from dataclasses import dataclass, replace

@dataclass(frozen=True)
class Config:
    host: str
    port: int
    tags: tuple = ()

base = Config("localhost", 8080, tags=("dev",))
prod = replace(base, host="prod.server.com", port=443)

print(base)  # Config(host='localhost', port=8080, tags=('dev',))
print(prod)  # Config(host='prod.server.com', port=443, tags=('dev',))
```

### The shallow copy danger

`replace()` only copies the top level. Nested mutable objects are still shared:

```python
from dataclasses import dataclass, replace, field
import copy

@dataclass
class State:
    name: str
    data: list = field(default_factory=list)

original = State("original", data=[1, 2, 3])
shallow  = replace(original, name="shallow_copy")

shallow.data.append(99)
print(original.data)  # [1, 2, 3, 99] — original was affected!

# Use deepcopy when nested mutables are involved
deep = copy.deepcopy(original)
deep.data.append(999)
print(original.data)  # [1, 2, 3, 99] — unaffected by deep copy mutation
```

> **Rule:** Use `replace()` for simple value overrides on shallow structures.
> Use `copy.deepcopy()` when your dataclass contains nested mutable containers.

---

## 8. Quick Reference

| Scenario | Use |
|---|---|
| Simple data container, fields change over time | `@dataclass` (mutable) |
| Data that must never change after creation | `@dataclass(frozen=True)` |
| Using a dataclass as a dict key or in a set | `frozen=True` required |
| Field that defaults to a list, dict, or set | `field(default_factory=list)` |
| Returning cached mutable objects safely | `copy.deepcopy()` on the cached value |
| Creating a modified copy of a frozen dataclass | `dataclasses.replace()` |
| Heuristic/intermediate computation state | Mutable `@dataclass`, freeze before caching |
| Nested mutable fields needing true isolation | `copy.deepcopy()` |


---

## 9. Mini-Project — Letter Density Set Predictor

Everything in this guide comes together in one place if you read the companion script
`density_predictor.py`. It polls the USGS real-time earthquake feed every 30 seconds,
classifies each event into a letter class by magnitude, and predicts the next poll's
density per class using an Exponentially Weighted Average rate-of-change. No ML libraries —
pure Python and statistics.

The dataclass roles map directly onto every concept in this guide:

```
DensityEvent  → frozen=True  : one seismic event, immutable after creation
                               safe to use as a dict key, put in a set, cache freely

DensitySet    → frozen=True  : a rolling window snapshot of all events
                               tuple field (not list) — frozen is only as strong as your field types
                               used as a cache key by window_start timestamp

LetterState   → mutable      : per-letter EWA rate tracking — intentional mutation
                               this is the heuristic state described in section 6
                               never cached directly, values are copied out before storing

ActiveArea    → mutable      : per-region accumulator, rebuilt every poll
                               discarded after display, never touches the cache

PatternModel  → mutable      : the live model — holds history, letter states, prediction cache
                               prediction_cache stores float copies not LetterState references
                               (the cache mutation safeguard from section 5)
```

The prediction cache is worth looking at specifically because it demonstrates the
section 5 finding in practice:

```python
# From update_model() in density_predictor.py

# Store the float value, NOT a reference to the mutable LetterState.
# If we stored 'state' directly, any future mutation to state.prediction
# would silently corrupt the cached value — the bug from section 5.
predictions[letter] = predicted # Use float copies

model.prediction_cache[density_set.window_start] = dict(predictions)
# dict() creates a shallow copy of predictions — safe because all values
# are floats (immutable). If values were mutable objects, deepcopy would
# be required here instead.
```

The `DensitySet` frozen / tuple pattern is also worth noting:

```python
@dataclass(frozen=True)
class DensitySet:
    events:       tuple[DensityEvent, ...]   # tuple, not list
    window_start: float
    window_end:   float
```

If `events` were a `list`, `frozen=True` would still block `density_set.events = [...]`
but would allow `density_set.events.append(...)` — the list itself is mutable. Using
`tuple` closes that gap. The same principle applies anywhere you use `frozen=True` with
a container field: the immutability guarantee only extends to the types you put inside it.

The script is standalone, requires no pip installs, and runs on Python 3.10+. It is a
direct demonstration that the patterns in this guide are not academic — they come up
naturally the moment you start building anything that polls, caches, and predicts.

---

## Summary

- **Mutable dataclasses** are flexible but require discipline — especially around caching and shared references.
- **Frozen dataclasses** enforce immutability at runtime, enable hashing, and make caching safe by design.
- **`field(default_factory=...)`** is not optional when your default is a mutable container — it's a correctness requirement.
- **Cache mutation bugs** are silent and hard to trace. The fix is either defensive copying or freezing the cached type.
- **Heuristics** show a legitimate use of mutable state — the key is knowing *when* to freeze a snapshot vs. continuing to update in place.
- **Frozen is only as strong as your field types** — a `frozen=True` dataclass with a `list` field is still mutable through that list. Use `tuple` for immutable sequences inside frozen dataclasses.

---

*Written as part of an exploration of what I know with Claude.*
