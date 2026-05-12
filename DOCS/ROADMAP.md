# TokenGate — Roadmap

This document outlines the planned development direction for TokenGate beyond its   
current beta.  

Items are organized by milestone rather than timeline, as this is an active solo project.  

Each milestone builds on the last — earlier phases establish the infrastructure that later   
phases depend on.

---

## Milestone 1 — Tokenize Everything

> *The goal: a user should be able to decorate their entire codebase — main thread   
> callers included — giving TokenGate full accountability over all execution, enabling   
> a full-application pause and laying the groundwork for main thread optimization   
> in the future.*

Today TokenGate only sees what is explicitly submitted as a token. Functions that run on   
the main thread — the callers that drive the application's top-level logic — are invisible   
to the system. This milestone changes that by making full-codebase decoration possible   
and meaningful.

### What "Tokenize Everything" Actually Means

This is not just about wrapping `main()`. The intent is that **every distinct operation — any   
function that performs a return — carries a decorator**, so TokenGate has a complete picture   
of all meaningful work being done, whether it runs on a background worker or on the main   
thread itself.

The decoration requirement is scoped deliberately. Internal utility calls, simple expressions,   
and third-party library internals are not the target. The target is any function in the   
developer's own codebase that produces a result and could be considered a unit of work.  
These are the functions that matter for pausing correctly.

This full visibility serves two purposes. First, it makes a true full-application pause possible,   
because the system knows about every returning operation. Second, it creates the data foundation  
needed to optimize main thread calls in a future pass — you cannot optimize what you cannot see.

> **Every distinct operation that performs a return must be decorated for the full-application  
> pause to be safe. An undecorated returning operation has no awareness of the paused state —   
> it will execute anyway, potentially touching shared state or triggering side effects while the  
> rest of the application believes everything is halted. This creates gaps in both safety and   
> security that cannot be detected or recovered from automatically. This requirement will be   
> handled.**

```python
# Every distinct returning operation in the application carries a decorator.
# Main thread callers use a distinct decorator that marks them as running on the main thread,
# not as background token tasks.

@main_thread_caller          # Runs on the main thread, visible to TokenGate
def process_input(data):
    validate(data)           # Decorated — distinct returning operation, child of this caller
    store_result(data)       # Decorated — distinct returning operation, child of this caller

@task_token_main(operation_type='validate')
def validate(data):
    ...

@task_token_main(operation_type='store')
def store_result(data):
    ...
```

### Main Thread Controls (Opt-In)

Full application control — including the ability to pause the main thread alongside all   
workers — is an **explicit opt-in**. A plain `@main_thread_caller` decorator registers   
the function with TokenGate but grants no pause or kill authority. That behavior requires   
opting in deliberately, because when active it halts everything.

When opted in, the contract is stated clearly to the developer:

> *When used, the whole application will store all tasks as tokens up to a   
> configured maximum. Tasks that arrive beyond that maximum are discarded.*

There is no silent backpressure, no hidden blocking, and no ambiguity at the  
capacity ceiling — overflow is dropped and the application continues.

```python
@tokenize_main(main_thread_controls=True, backlog_limit=150)
def main():
    process_input(data)
    run_next_frame(data)
```

**Full-application pause:**
- When a pause is issued, it propagates to all threaded workers and to the main    
thread's own call submissions — everything stops together.
- The application reaches a clean suspended state: no new work starts, all currently  
executing work finishes its current unit, and further submissions queue into the    
backlog up to the configured limit.
- This is the only mode where pausing the main thread is valid, because the developer   
has explicitly accepted that the whole application will suspend.

### Locked Call Chains

When a call chain is active, all tokens within it are linked under a chain ID, forming  
a unit that the system can pause, store, and kill as a whole.

**Pause behavior — submission halts at chain boundaries:**
- Pausing never cuts a chain in progress. The system waits for the currently executing   
lead token's chain to complete, then pauses at the **next incoming lead caller**.   
Mid-chain interruption is not permitted.  
- New lead callers and their call sites are held in a **backlog deque** while the system    
is paused. The deque capacity is user-configurable with a default of **150 lead task groups**.
- The capacity cap applies **only to lead tokens**. Children belonging to a stored lead    
group are always admitted into the deque without limit — a chain is stored as a whole   
unit, so child count never causes rejection.  
- Lead task groups that arrive after the deque is full are **discarded**. This is the   
defined behavior; no silent queuing beyond the limit occurs.  
- While paused, the **kill option becomes available** against lead task groups stored   
in the backlog.

**Kill behavior — backlog only:**
- Kill targets lead task groups sitting in the deque and their associated children.   
In-flight tokens are never touched.
- Because pauses only ever land between chains, any killed group represents work   
that never started — no partial execution state is left behind.
- Once the backlog is cleared, the chain can either resume cleanly or be fully stopped.

**Chain structure:**
- Each call chain gets a **chain ID** assigned at the root token's creation.
- All child tokens inherit the chain ID and register themselves as chain members.
- Chain locks are designed to be transparent — decorated functions require no   
structural changes to participate.

### Future: Main Thread Optimization

Full-codebase decoration gives TokenGate the call graph data needed to optimize   
main thread execution in a later pass. Specific optimization strategies are out  
of scope for this milestone — the goal here is visibility and correctness first.  
Optimization follows once the decorated call graph can be trusted to be complete.

---

## Milestone 2 — TokenCache

> *The goal: frequently used tokens should not have to travel through the full   
> admission and routing pipeline on every call.*

Today, every call to a decorated function creates a new token, queues it for admission,   
waits for a worker to pick it up, and executes. For high-frequency, low-latency   
operations this pipeline cost is measurable overhead.

### class TokenCache

`TokenCache` stores **pre-warmed token shells** — objects that have already completed the   
setup phases (code inspection, complexity scoring, core affinity resolution) and are ready   
to activate instantly on call.

```python
# Proposed interface
class TokenCache:
    def __init__(self, func: Callable, capacity: int = 32): ...
    def prime(self) -> None: ...           # Pre-warm the cache
    def acquire(self) -> TaskToken: ...    # Pull a ready token, bypass gatherer
    def release(self, token: TaskToken) -> None: ...  # Return a completed token shell for reuse
    def resize(self, new_capacity: int) -> None: ...
    def stats(self) -> CacheStats: ...
```

### Design Considerations

- Cached tokens are **reusable shells**: the function reference, affinity assignment, and  
metadata are fixed; only the args, result, and state are reset between uses.
- The cache is **function-scoped** — one `TokenCache` per decorated function. This keeps   
the performance benefit targeted and avoids cross-function interference.
- Cache size is user-controlled. The default should be conservative (e.g. 16–32 tokens)   
to avoid over-allocating for functions that are rarely hot.
- If the cache is exhausted, calls fall back gracefully to the standard admission pipeline  
rather than failing.
- Integration with the WebSocket dashboard: expose cache hit/miss rates and active pool   
size per function in the telemetry panel.

### Decorator Integration

```python
# Opt-in via tag — no new decorator required
@task_token_guard(
    operation_type='hot_path',
    tags={'weight': 'light', 'cache': True, 'cache_size': 32}
)
def hot_function(data):
    ...
```

---

## Milestone 3 — Distributed Compute

> *The goal: allow users to contribute idle CPU time to a shared pool, and allow other  
> users to submit work to that pool in exchange for a guaranteed execution timeslot on   
> capable hardware.*

This is the most ambitious milestone and is explicitly a **research and design phase**   
before any implementation begins. The architecture has significant security, privacy,   
and reliability implications that need to be resolved on paper first.

### Concept

Two roles exist in the distributed system:

**Providers** — machines with spare CPU capacity that opt in to accepting remote work.  
**Consumers** — users who submit jobs that are either too intensive for their local   
hardware or require a guaranteed latency window.

Consumers purchase a **guaranteed timeslot**: a reservation on provider hardware that   
ensures their job starts within a defined window and runs to completion without preemption.

### Provider Side

- Providers run a lightweight **TokenGate Node** process that advertises available capacity  
(core count, weight tiers, current load) to the network.
- Providers define their own **acceptance policy**: which operation types they will accept,  
maximum job duration, and pricing.
- All submitted code is executed in an **isolated sandbox** (containerized or VM-backed).  
Providers never execute raw user code in their host environment.
- Providers can set automatic kill conditions (e.g. memory ceiling, wall-clock limit) as a   
safety floor independent of consumer-supplied parameters.

### Consumer Side

- Consumers submit a job bundle: the decorated function(s), a dependency manifest, and the   
desired execution parameters (timeslot window, weight tier, timeout).
- The submission is **screened** before entering the marketplace:
  - Static analysis pass on the submitted code (no network calls, no filesystem writes   
  outside a sandboxed scratch directory, no subprocess spawning without explicit opt-in).
  - Reputation check on the consumer account.
  - Cryptographic signing of the bundle so the provider can verify it was not modified   
  after screening.
- On acceptance, the consumer receives a **timeslot token**: a signed receipt that guarantees   
execution will begin within the agreed window.
- Results are returned encrypted to the consumer and are not visible to the provider node.  

### Marketplace

- A lightweight **registry service** matches consumers to available providers based on job   
requirements and provider policy.
- Pricing is set per provider. The registry takes no cut in the initial version — this is a  
coordination layer, not a commercial platform.
- The registry is the only centralized component. Provider ↔ consumer execution is peer-to-peer  
once the match is made.

### Open Questions (to resolve before implementation)

- Sandboxing strategy: Docker, Firecracker, or WASM-based isolation?
- How to handle long-running jobs if a provider goes offline mid-execution.
- Key management for result encryption.
- Rate limiting and abuse prevention on the screening pipeline.
- Whether timeslot tokens should be transferable or strictly bound to the purchasing consumer.

---

## Milestone 4 — TokenClient

> *The goal: a Flask-based client that serves as an interactive interface, project explorer,  
> and execution portal for the TokenGate ecosystem — with online integration for discoverable   
> apps and lightweight hosted experiences.*

`TokenClient` is a **Flask server** that runs locally and connects to an online registry. It   
is the public-facing layer of the TokenGate ecosystem — the place where users share projects,   
find others, and run approved TokenGate-based applications without touching the underlying   
code. The same interface also hosts lightweight web experiences like small games, making   
`TokenClient` a usable destination in its own right rather than purely a management tool.

### Flask Server

- `TokenClient` runs as a local Flask process and serves a web interface at `localhost`   
(port configurable).
- The interface extends the existing WebSocket dashboard with a broader scope:
  - Browse and launch installed TokenGate projects.
  - Monitor live telemetry across multiple running projects simultaneously.
  - Submit jobs to the distributed compute network (Milestone 3) directly from the UI.
  - Manage provider node configuration if contributing capacity.
- WebSocket push keeps all panels live without polling, consistent with the existing   
dashboard approach.

### Hosted Experiences

`TokenClient` includes a small **Arcade** section — lightweight browser-playable applications   
served directly from the Flask server. These are intentionally simple: the goal is to give the  
client a genuine reason to be open beyond project management, and to demonstrate that TokenGate-  
backed applications can be approachable and fun.

Initial targets:
- **Snake** — classic, minimal state, a clean showcase for token-managed game loop updates.
- **Minesweeper** — slightly more complex state, good for demonstrating event-driven token dispatch.

Community-built arcade entries can be submitted through the same approval pipeline as projects   
(see below). This gives developers a low-stakes first target for publishing something to the  
ecosystem.

### Project Explorer

- An online registry of **approved TokenGate projects** — open-source applications built on   
top of TokenGate that others can discover and download.
- Projects are submitted for approval before appearing in the explorer. Approval checks:
  - Code review for obvious safety issues.
  - Licensing compatibility.
  - Basic functionality verification (the project runs without errors against a reference workload).
- Approved projects get a **verified badge** and a canonical download URL managed through the  
registry.
- The explorer is searchable and filterable by category, weight profile, and platform requirements.

### One-Click Run

- Approved projects can be launched directly from `TokenClient` without manual setup.
- `TokenClient` handles dependency installation, `OperationsCoordinator` initialization,   
and WebSocket dashboard launch automatically.
- Users can inspect a project's token graph and operation types before running it.

### Developer Publishing Flow

```
Local project
  → Submit to registry for approval
    → Review pass
      → Listed in TokenClient explorer
        → Anyone can discover, download, and run with one click
```

---

## Ongoing

The following are not milestones with a defined completion point — they are continuous   
priorities across all development.

- **Documentation** — DOCS should stay current with every meaningful change. Accuracy is   
more important than completeness.
- **WebSocket dashboard** — each milestone above adds new observable state; the dashboard  
should grow to reflect it.
- **Backward compatibility** — existing decorated functions should continue to work without  
changes as new features are introduced. New behavior is always opt-in.
- **Performance baseline** — the proof-of-concept benchmarks (5.87x sustained concurrency,   
sub-40ms p95 latency) are the floor. No milestone should regress them.

---

*TokenGate: the limit isn't speed, it's parallelism.*
