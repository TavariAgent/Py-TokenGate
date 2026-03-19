#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
 Letter Density Set Predictor — USGS Seismic Edition
 A mini-project companion to: "An Explanative Guide to Cache Mutations,
 Heuristics and Mutability/Immutability Using dataclasses"
───────────────────────────────────────────────────────────────────────────────
 Concept
 ───────
 Seismic events are bucketed into letter classes by magnitude range.
 Each letter (A–H) represents an event class. The density of that letter
 is the average magnitude of events in that class over a rolling time window.

 The model tracks how density changes across polls (rate of change), smooths
 that rate using an Exponentially Weighted Average (EWA), and uses it to
 predict what the next poll's density will look like per letter.

 Prediction formula:
   rate          = (current_density − prev_density) / delta_time
   smoothed_rate = α × rate + (1 − α) × prev_smoothed_rate
   prediction    = current_density + smoothed_rate × POLL_INTERVAL

 Dataclass roles (ties directly into the guide):
   DensityEvent  → frozen: immutable per-event snapshot, safe to cache/hash
   DensitySet    → frozen: immutable time-window snapshot, used as cache key
   LetterState   → mutable: intentional mutation during active computation
   ActiveArea    → mutable: live region tracking, updated each poll
   PatternModel  → mutable: accumulates history, holds prediction cache

 Location flagging
 ─────────────────
 Every USGS event carries a human-readable `place` string, e.g.:
   "15km NW of Ridgecrest, California"
   "126km NNE of Kodiak, Alaska"
   "South of the Fiji Islands"
 We parse these into a short region label, count events per region across
 the rolling window, and surface the hottest active areas each poll.

 FILTER_AREA (config below) lets you narrow the density table to a specific
 region substring — e.g. "Alaska" or "Japan". Set to None for global view.
 The active areas block always shows the full global picture regardless.

 Requires: Python 3.10+  (stdlib only — no pip installs)
═══════════════════════════════════════════════════════════════════════════════
"""

import time
import json
import urllib.request
from dataclasses import dataclass, field
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────────────

POLL_INTERVAL    = 30           # Seconds between USGS fetches
BATCH_WINDOW     = 60 * 5      # Rolling window: 5 minutes of events
ALPHA            = 0.35         # EWA smoothing factor (0=sluggish, 1=reactive)
ACTIVE_AREA_TOP  = 8            # How many active areas to show in the display
FILTER_AREA: str | None = None        # e.g. "Alaska" — narrows density table. None = global
USGS_URL         = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
)

# ── LETTER MAP ────────────────────────────────────────────────────────────────
# Each letter represents a magnitude class.
# Density = average magnitude of events in that class within the time window.

MAGNITUDE_LETTERS: list[tuple[float, str]] = [
    (1.0, "A"),   # micro
    (2.0, "B"),   # minor
    (3.0, "C"),   # light
    (4.0, "D"),   # moderate
    (5.0, "E"),   # strong
    (6.0, "F"),   # major
    (7.0, "G"),   # great
]

LETTER_LABELS: dict[str, str] = {
    "A": "micro    < 1.0",
    "B": "minor    1.0–2.0",
    "C": "light    2.0–3.0",
    "D": "moderate 3.0–4.0",
    "E": "strong   4.0–5.0",
    "F": "major    5.0–6.0",
    "G": "great    6.0–7.0",
    "H": "massive  7.0+",
}


def magnitude_to_letter(mag: float) -> str:
    for threshold, letter in MAGNITUDE_LETTERS:
        if mag < threshold:
            return letter
    return "H"


# ── DATACLASSES ───────────────────────────────────────────────────────────────

# ── REGION EXTRACTION ────────────────────────────────────────────────────────
# USGS `place` strings look like:
#   "15km NW of Ridgecrest, California"  →  "California"
#   "South of the Fiji Islands"          →  "Fiji Islands"
#   "126km NNE of Kodiak, Alaska"        →  "Alaska"
#   "Reykjanes Ridge"                    →  "Reykjanes Ridge"
#
# Strategy: split on " of " → take the right half → strip "the " prefix →
# if a comma exists, take only the last token (the broad region name).

def extract_region(place: str) -> str:
    if not place:
        return "Unknown"
    chunk = place
    if " of " in place:
        chunk = place.split(" of ", 1)[1]
        chunk = chunk.lstrip("the ").lstrip("The ")
    if "," in chunk:
        chunk = chunk.split(",")[-1].strip()
    return chunk.strip() or "Unknown"


# ── DATACLASSES ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DensityEvent:
    """
    A single seismic event as an immutable snapshot.
    frozen=True: safe to cache, use as dict key, or put in a set.
    The letter, density, and place can never be mutated after construction.
    """
    letter:    str
    density:   float    # magnitude value
    timestamp: float    # unix epoch (seconds)
    place:     str = "" # human-readable location from USGS e.g. "Alaska"


@dataclass(frozen=True)
class DensitySet:
    """
    An immutable snapshot of one poll window's grouped events.
    Frozen so it is safe to use as a cache key and store in prediction_cache.

    NOTE: events is typed as tuple — if this were a list, frozen=True would
    still allow mutation through the list. Frozen is only as strong as
    the types inside it.
    """
    events:       tuple[DensityEvent, ...]
    window_start: float
    window_end:   float

    def letter_densities(self) -> dict[str, float]:
        """Average density per letter class across all events in this window."""
        groups: dict[str, list[float]] = defaultdict(list)
        for e in self.events:
            groups[e.letter].append(e.density)
        return {k: sum(v) / len(v) for k, v in sorted(groups.items())}

    def letter_counts(self) -> dict[str, int]:
        """Number of events per letter class — the raw frequency/density."""
        counts: dict[str, int] = defaultdict(int)
        for e in self.events:
            counts[e.letter] += 1
        return dict(counts)


@dataclass
class LetterState:
    """
    Per-letter mutable tracking state.
    Intentionally mutable — this is where active computation happens.
    Values here are mid-flight heuristics, not final results.
    Freeze or snapshot before caching (see PatternModel.prediction_cache).
    """
    letter:        str
    last_density:  float = 0.0
    last_time:     float = 0.0
    smoothed_rate: float = 0.0    # EWA of rate-of-change across polls
    prediction:    float = 0.0    # Last predicted value for this letter


@dataclass
class ActiveArea:
    """
    Mutable per-region accumulator, rebuilt fresh each poll from the
    current window's events.

    Intentionally mutable — it exists only during the display pass and is
    discarded after. It is never cached, so no cache mutation risk here.
    peak_magnitude lets us flag if a region produced anything notable.
    """
    region:          str
    event_count:     int   = 0
    total_magnitude: float = 0.0
    peak_magnitude:  float = 0.0
    last_seen:       float = 0.0

    @property
    def avg_magnitude(self) -> float:
        return self.total_magnitude / self.event_count if self.event_count else 0.0


@dataclass
class PatternModel:
    """
    Mutable model accumulating DensitySet history and per-letter state.

    prediction_cache stores frozen snapshots keyed by window_start.
    This is safe because:
      - keys are floats (immutable)
      - values are plain dicts (snapshots copied at cache time, not references)

    If we cached LetterState objects directly here, any mutation to the
    live state would silently corrupt previously cached predictions —
    this is the cache mutation bug from the guide. We avoid it by
    storing copies of the prediction values (plain floats), not references
    to the mutable LetterState objects.
    """
    letter_states:    dict[str, LetterState] = field(default_factory=dict)
    history:          list[DensitySet]       = field(default_factory=list)
    prediction_cache: dict[float, dict]      = field(default_factory=dict)

    def get_or_init(self, letter: str) -> LetterState:
        if letter not in self.letter_states:
            self.letter_states[letter] = LetterState(letter=letter)
        return self.letter_states[letter]


# ── USGS FETCH ────────────────────────────────────────────────────────────────

def fetch_events() -> list[DensityEvent]:
    """
    Poll USGS GeoJSON feed and return a list of frozen DensityEvents.
    All raw dicts from the API are immediately converted to frozen dataclasses —
    mutable boundary kept at the edge, frozen objects throughout the rest.
    The `place` field is preserved verbatim from USGS; region extraction
    happens downstream so the raw string is always available.
    """
    try:
        with urllib.request.urlopen(USGS_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  [fetch error] {exc}")
        return []

    events: list[DensityEvent] = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        mag   = props.get("mag")
        ts    = props.get("time")
        place = props.get("place") or ""
        if mag is None or ts is None:
            continue
        mag = float(mag)
        ts  = float(ts) / 1000.0    # USGS returns milliseconds
        if mag < 0:
            continue                 # occasional negative-magnitude readings in USGS
        events.append(DensityEvent(
            letter    = magnitude_to_letter(mag),
            density   = mag,
            timestamp = ts,
            place     = place,
        ))
    return events


# ── BATCH BUILDER ─────────────────────────────────────────────────────────────

def build_density_set(events: list[DensityEvent], now: float) -> DensitySet:
    """
    Filter events into the current rolling window and return a frozen DensitySet.
    This is the snapshot boundary: mutable list in → immutable DensitySet out.
    """
    window_start = now - BATCH_WINDOW
    in_window    = tuple(e for e in events if e.timestamp >= window_start)
    return DensitySet(
        events       = in_window,
        window_start = window_start,
        window_end   = now,
    )

def build_active_areas(events: list[DensityEvent], now: float) -> list[ActiveArea]:
    """
    Scan the current window's events and accumulate per-region ActiveArea state.
    ActiveArea objects are mutable during this pass, then returned as a
    sorted snapshot — never cached, so no cache mutation risk.
    """
    window_start = now - BATCH_WINDOW
    areas: dict[str, ActiveArea] = {}

    for e in events:
        if e.timestamp < window_start:
            continue
        region = extract_region(e.place)
        if region not in areas:
            areas[region] = ActiveArea(region=region)
        a = areas[region]
        a.event_count     += 1
        a.total_magnitude += e.density
        a.peak_magnitude   = max(a.peak_magnitude, e.density)
        a.last_seen        = max(a.last_seen, e.timestamp)

    return sorted(areas.values(), key=lambda a: a.event_count, reverse=True)




def update_model(model: PatternModel, density_set: DensitySet) -> dict[str, float]:
    """
    Update the mutable PatternModel with a new frozen DensitySet.

    For each letter class:
      1. Compute rate of change since the last poll
      2. Smooth the rate with EWA (α controls how reactive vs. historical)
      3. Predict next density = current + smoothed_rate × poll_interval

    The smoothed_rate is the multiplier. Higher α = reacts faster to sudden
    density spikes. Lower α = trusts historical trend more, dampens noise.

    Predictions are stored as plain float copies — not references to
    LetterState — so future mutations to LetterState don't corrupt
    previously cached prediction values.
    """
    now              = density_set.window_end
    letter_densities = density_set.letter_densities()
    predictions: dict[str, float] = {}

    for letter, density in letter_densities.items():
        state = model.get_or_init(letter)

        # Compute rate of change if we have a previous data point
        if state.last_time > 0:
            delta_time = now - state.last_time
            if delta_time > 0:
                rate               = (density - state.last_density) / delta_time
                state.smoothed_rate = (
                    ALPHA * rate + (1.0 - ALPHA) * state.smoothed_rate
                )

        # Predict: extrapolate from current density along the smoothed rate
        predicted = density + state.smoothed_rate * POLL_INTERVAL
        predicted = max(0.0, predicted)    # magnitude can't be negative

        # Update mutable state (intentional mutation — active computation)
        state.last_density = density
        state.last_time    = now
        state.prediction   = predicted

        # Store the float value, NOT a reference to state
        # (This is the cache mutation safeguard from the guide)
        predictions[letter] = predicted

    # Append frozen snapshot to history
    model.history.append(density_set)

    # Cache this poll's predictions keyed by window start
    model.prediction_cache[density_set.window_start] = dict(predictions)

    return predictions


# ── DISPLAY ───────────────────────────────────────────────────────────────────
#
# Layout constants — all row widths derive from W.
# Changing W here is the only thing needed to resize the whole display.
#
#   W        : inner box width (between ║ chars)
#   BAR_W    : fixed bar character count — same for every row
#   MAG_MAX  : maximum magnitude used for bar scaling
#              (bar is value/MAG_MAX × BAR_W — always proportional)
#
# Every row is built as a plain string then passed through _row(), which
# clamps or pads to exactly W chars before adding the border. This means
# no row can ever overflow or fall short — the right ║ is always in the
# same column regardless of content length.

W       = 76
BAR_W   = 18
MAG_MAX = 9.0   # H class starts at 7.0; 9.0 gives headroom without wasting bar space


def _bar(value: float) -> str:
    """Fixed-width bar scaled to MAG_MAX. Always returns exactly BAR_W chars."""
    filled = min(int((value / MAG_MAX) * BAR_W), BAR_W)
    return "█" * filled + "░" * (BAR_W - filled)


def _row(content: str) -> str:
    """
    Wrap content in box borders, always at exactly W chars wide.
    Truncates if content is too long, pads with spaces if too short.
    The right border is always in column W+1 — no overflow possible.
    """
    return "║" + content[:W].ljust(W) + "║"


def _sep() -> str:
    return "╠" + "═" * W + "╣"


def _divider() -> str:
    return _row("  " + "─" * (W - 4))


def _density_rows(
    letter_densities: dict[str, float],
    letter_counts:    dict[str, int],
    model:            PatternModel,
) -> None:
    """Print density rows — shared between global and filtered table blocks."""
    if not letter_densities:
        print(_row("  — no events in window —"))
        return
    for letter in sorted(letter_densities):
        density = letter_densities[letter]
        count   = letter_counts.get(letter, 0)
        state   = model.letter_states.get(letter)
        rate_s  = f"{state.smoothed_rate:+.4f}" if state and state.last_time > 0 else "     —"
        label   = LETTER_LABELS.get(letter, "?")
        bar     = _bar(density)
        print(_row(f"  {letter:<4}  {label:<20}  {count:>3}  {density:>8.3f}  {rate_s:>8}  {bar}"))


def display(
    density_set:      DensitySet,
    predictions:      dict[str, float],
    prev_predictions: dict[str, float],
    active_areas:     list[ActiveArea],
    all_events:       list[DensityEvent],
    model:            PatternModel,
    poll_num:         int,
) -> None:
    letter_densities = density_set.letter_densities()
    letter_counts    = density_set.letter_counts()
    total_events     = len(density_set.events)
    now_str          = time.strftime("%H:%M:%S", time.localtime(density_set.window_end))
    total_polls      = len(model.history)
    filter_label     = f"  ·  filter: {FILTER_AREA}" if FILTER_AREA else ""

    print()
    print("╔" + "═" * W + "╗")
    print(_row(f"  Poll #{poll_num:<4}  {now_str}  ·  {total_events} events  ·  {BATCH_WINDOW // 60}-min window  ·  {total_polls} polls{filter_label}"))
    print(_sep())

    # ── Global density table ──────────────────────────────────────────────────
    scope = "GLOBAL" if not FILTER_AREA else "GLOBAL  (set FILTER_AREA to narrow)"
    print(_row(f"  {scope}"))
    print(_row(f"  {'LTR':<4}  {'Type':<20}  {'n':>3}  {'Density':>8}  {'Rate/s':>8}  {'Bar':<{BAR_W}}"))
    print(_divider())
    _density_rows(letter_densities, letter_counts, model)

    # ── Filtered density table (only when FILTER_AREA is set) ─────────────────
    if FILTER_AREA:
        print(_sep())
        print(_row(f"  FILTER: {FILTER_AREA.upper()}"))
        print(_row(f"  {'LTR':<4}  {'Type':<20}  {'n':>3}  {'Density':>8}  {'Rate/s':>8}  {'Bar':<{BAR_W}}"))
        print(_divider())

        wstart  = density_set.window_start
        f_lower = FILTER_AREA.lower()
        f_events = [
            e for e in all_events
            if e.timestamp >= wstart and f_lower in e.place.lower()
        ]
        f_groups: dict[str, list[float]] = defaultdict(list)
        f_counts: dict[str, int]         = defaultdict(int)
        for e in f_events:
            f_groups[e.letter].append(e.density)
            f_counts[e.letter] += 1
        f_densities = {k: sum(v) / len(v) for k, v in sorted(f_groups.items())}
        _density_rows(f_densities, dict(f_counts), model)

    # ── Predictions ───────────────────────────────────────────────────────────
    print(_sep())
    print(_row(f"  {'LTR':<4}  {'Predicted Next':>14}  {'Δ from Expected':>16}  {'Forecast Bar':<{BAR_W}}"))
    print(_divider())

    for letter in sorted(predictions):
        pred      = predictions[letter]
        prev_pred = prev_predictions.get(letter)
        actual    = letter_densities.get(letter, 0.0)
        diff_s    = f"{actual - prev_pred:+.3f}" if prev_pred is not None else "     first"
        bar       = _bar(pred)
        print(_row(f"  {letter:<4}  {pred:>14.4f}  {diff_s:>16}  {bar}"))

    # ── Active areas ──────────────────────────────────────────────────────────
    print(_sep())
    print(_row("  ACTIVE AREAS  (current window, ranked by event count)"))
    print(_row(f"  {'Region':<26}  {'n':>4}  {'Avg Mag':>8}  {'Peak':>6}  {'Last Seen':>10}"))
    print(_divider())

    shown = active_areas[:ACTIVE_AREA_TOP]
    for a in shown:
        age_s     = time.strftime("%H:%M:%S", time.localtime(a.last_seen))
        peak_flag = " !" if a.peak_magnitude >= 4.0 else "  "
        region_s  = a.region[:26]
        print(_row(f"  {region_s:<26}  {a.event_count:>4}  {a.avg_magnitude:>8.3f}  {a.peak_magnitude:>5.2f}{peak_flag}  {age_s:>10}"))

    if not shown:
        print(_row("  — no regional data in window —"))

    overflow = len(active_areas) - ACTIVE_AREA_TOP
    if overflow > 0:
        print(_row(f"    + {overflow} more regions active"))

    print("╚" + "═" * W + "╝")
    filter_hint = "  set FILTER_AREA = 'RegionName' to narrow  |  " if not FILTER_AREA else ""
    print(f"  {filter_hint}EWA α={ALPHA}  |  Ctrl+C to stop")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("  Letter Density Set Predictor — USGS Seismic Feed")
    print("  " + "─" * 50)
    print("  Letters  : magnitude classes A (micro) → H (massive)")
    print("  Density  : average magnitude of events in each class")
    print("  Rate     : smoothed EWA rate-of-change per second")
    print("  Predicted: density + rate × poll_interval")
    print("  Δ Expect : actual this poll − what was predicted last poll")
    print("  Areas    : live global regions ranked by event count, ! = peak ≥ 4.0")
    print(f"\n  Poll interval : {POLL_INTERVAL}s")
    print(f"  Batch window  : {BATCH_WINDOW // 60} minutes")
    print(f"  EWA alpha     : {ALPHA}  (raise to react faster, lower to smooth more)")
    print(f"  Active areas  : top {ACTIVE_AREA_TOP} shown  |  set FILTER_AREA to narrow density table")
    print(f"  Source        : {USGS_URL}")
    print()

    model: PatternModel     = PatternModel()
    prev_predictions: dict  = {}
    poll_num: int           = 0

    while True:
        poll_num += 1
        now    = time.time()
        print(f"\n  [fetching poll #{poll_num}...]")

        events = fetch_events()

        if not events:
            print(f"  No data. Retrying in {POLL_INTERVAL}s.")
            time.sleep(POLL_INTERVAL)
            continue

        density_set  = build_density_set(events, now)
        active_areas = build_active_areas(events, now)
        predictions  = update_model(model, density_set)

        display(density_set, predictions, prev_predictions, active_areas, events, model, poll_num)

        # Safe copy — plain dict of floats, not references to LetterState
        prev_predictions = dict(predictions)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Stopped.")