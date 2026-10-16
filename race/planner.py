"""
Stop planner: estimates the rest-of-race time of every sensible 0, 1 and 2 stop strategy so the
engineer compares concrete options. Times are relative (base pace and fuel cancel out between
plans) and ignore track position, which the engineer weighs with the rejoin prediction.
"""
from .pace import field_compound_stats, stint_pace

DRY = ("SOFT", "MEDIUM", "HARD")
WET = ("INTER", "WET")

# Defaults until the race shows its own numbers: fresh-tyre pace vs medium (s/lap),
# degradation (s/lap per lap of age) and wear (% per lap)
DEFAULT_PACE_OFFSET = {"SOFT": -0.55, "MEDIUM": 0.0, "HARD": 0.45, "INTER": 0.0, "WET": 0.6}
DEFAULT_DEG = {"SOFT": 0.085, "MEDIUM": 0.06, "HARD": 0.04, "INTER": 0.07, "WET": 0.06}
DEFAULT_WEAR_RATE = {"SOFT": 5.0, "MEDIUM": 3.0, "HARD": 2.2, "INTER": 4.0, "WET": 3.5}
# On a wet track, slicks lose this much per lap against intermediates until the field shows otherwise
WET_SLICK_PENALTY = 8.0
# With the rain stopped but this share of the field still on wet tyres, the track counts as drying
DRYING_SHARE = 0.3
CLIFF_WEAR = 70.0
# Pit laps within this many seconds of the best plan form its window
WINDOW_S = 1.0
CLIFF_PENALTY = 0.35
MIN_STINT = 3
MAX_DEG = 0.6
OPTIONS_SHOWN = 5


def _visual(name: str) -> str:
    name = (name or "").upper()
    return name if name in DRY + WET else "MEDIUM"


class _Model:
    """Per-compound pace offset, degradation and wear rate, calibrated from the race where possible."""

    def __init__(self, state, wet: bool):
        stats = field_compound_stats(state.histories.values())
        self.sources = {}
        self.offset, self.deg, self.wear = {}, {}, {}
        for c in DRY + WET:
            s = stats.get(c) or {}
            self.offset[c] = DEFAULT_PACE_OFFSET[c]
            self.deg[c] = min(s["deg"], MAX_DEG) if s.get("deg") and s["deg"] > 0 else DEFAULT_DEG[c]
            self.wear[c] = s["wear_rate"] if s.get("wear_rate") else DEFAULT_WEAR_RATE[c]
            self.sources[c] = "field" if s.get("deg") or s.get("wear_rate") else "default"

        if wet:
            inter = (stats.get("INTER") or {}).get("last_lap")
            for c in DRY:
                slick = (stats.get(c) or {}).get("last_lap")
                self.offset[c] = slick - inter if slick and inter else DEFAULT_PACE_OFFSET[c] + WET_SLICK_PENALTY
        else:
            for c in WET:
                self.offset[c] += WET_SLICK_PENALTY / 2

        self.car_factor = 1.0
        h = state.player_history()
        stint = h.current_stint() if h else None
        if stint:
            mine = stint_pace(h)
            c = _visual(stint.visual)
            field_wear = (stats.get(c) or {}).get("wear_rate")
            if mine["wear_rate"] and mine["wear_rate"] > 0:
                if field_wear:
                    self.car_factor = max(0.6, min(2.5, mine["wear_rate"] / field_wear))
                self.wear[c] = mine["wear_rate"]
                self.sources[c] = "your laps"
            if mine["deg"] and mine["deg"] > 0 and mine["laps"] >= 4:
                self.deg[c] = min(mine["deg"], MAX_DEG)
                self.sources[c] = "your laps"
            for other in DRY + WET:
                if other != c:
                    self.deg[other] *= self.car_factor
                    self.wear[other] *= self.car_factor

    def lap_cost(self, c: str, age: int, laps_to_cliff: float) -> float:
        beyond = max(0.0, age - laps_to_cliff)
        return self.offset[c] + self.deg[c] * age + CLIFF_PENALTY * beyond ** 1.5

    def prefix(self, c: str, start_age: int, laps_to_cliff: float, n: int) -> list:
        out = [0.0]
        for k in range(1, n + 1):
            out.append(out[-1] + self.lap_cost(c, start_age + k, laps_to_cliff))
        return out


def plan(state, loss_now: float, loss_green: float, in_force: dict | None = None) -> dict | None:
    total = state.total_laps()
    player = state.player()
    history = state.player_history()
    if not total or not player or not history or not state.lap:
        return None
    lap = state.lap
    remaining = total - lap + 1
    running = [c for c in state.cars if c.get("position") and (c.get("resultStatus") or "ACTIVE") == "ACTIVE"]
    on_wets = sum(1 for c in running if (c.get("visualCompound") or c.get("compound") or "").upper() in WET)
    raining = state.is_wet()
    drying = not raining and running and on_wets / len(running) >= DRYING_SHARE
    # Until cars on slicks show otherwise, a drying track is treated as still wet
    wet = raining or drying
    model = _Model(state, wet)

    current = _visual(player.get("visualCompound") or player.get("compound"))
    age = int(player.get("tyreAge") or 0)
    wear_values = list((player.get("tireWear") or {}).values())
    wear_now = sum(wear_values) / len(wear_values) if wear_values else age * model.wear[current]
    current_to_cliff = age + max(0.0, (CLIFF_WEAR - wear_now) / max(model.wear[current], 0.1))
    used = [_visual(v) for v in history.compounds_used()] or [current]

    choices = DRY + WET if drying else (WET if wet else DRY)
    fresh_life = {c: CLIFF_WEAR / max(model.wear[c], 0.1) for c in DRY + WET}
    fresh = {c: model.prefix(c, 0, fresh_life[c], remaining) for c in DRY + WET}
    cur = model.prefix(current, age, current_to_cliff, remaining)

    def rule_ok(compounds) -> bool:
        seq = used + list(compounds)
        if any(c in WET for c in seq) or wet:
            return True
        return len({c for c in seq if c in DRY}) >= 2

    def loss(pit_lap: int) -> float:
        return loss_now if pit_lap == lap else loss_green

    options = []
    if rule_ok([]):
        options.append({"stops": [], "time": cur[remaining]})
    for p in range(lap, total):
        m = p - lap + 1
        rest = remaining - m
        for c in choices:
            if rest >= 1 and rule_ok([c]):
                options.append({"stops": [(p, c)], "time": cur[m] + loss(p) + fresh[c][rest]})
            for p2 in range(p + MIN_STINT, total):
                mid, last = p2 - p, total - p2
                for c2 in choices:
                    if rule_ok([c, c2]):
                        options.append({
                            "stops": [(p, c), (p2, c2)],
                            "time": cur[m] + loss(p) + fresh[c][mid] + loss_green + fresh[c2][last],
                        })
    if not options:
        return None
    options.sort(key=lambda o: o["time"])
    best = options[0]["time"]

    def describe(o) -> str:
        if not o["stops"]:
            return f"no more stops, stay on {current}"
        legs = ", ".join(f"box L{p} for {c}" + (" (THIS LAP)" if p == lap else "") for p, c in o["stops"])
        return f"{len(o['stops'])} stop{'s' if len(o['stops']) > 1 else ''}: {legs}"

    def window(o) -> str:
        if not o["stops"]:
            return ""
        same = [x for x in options if [c for _, c in x["stops"]] == [c for _, c in o["stops"]]]
        laps = [x["stops"][0][0] for x in same if x["time"] - o["time"] <= WINDOW_S]
        lo, hi = min(laps), max(laps)
        return f" (first stop window L{lo}-L{hi})" if hi > lo else ""

    shown, seen = [], set()
    for o in options:
        key = (len(o["stops"]), tuple(c for _, c in o["stops"]))
        if key in seen:
            continue
        seen.add(key)
        shown.append({"plan": describe(o) + window(o), "delta": round(o["time"] - best, 1), "stops": o["stops"]})
        if len(shown) == OPTIONS_SHOWN:
            break

    box_now_by_compound = []
    for c in choices + ((current,) if current not in choices else ()):
        fits = [o for o in options if o["stops"] and o["stops"][0] == (lap, c)]
        if fits:
            o = min(fits, key=lambda o: o["time"])
            box_now_by_compound.append({"compound": c, "plan": describe(o), "delta": round(o["time"] - best, 1)})

    in_force_delta = None
    if in_force and in_force.get("next_compound") not in (None, "NONE") and (in_force.get("next_pit_lap") or 0) >= lap:
        first = (in_force["next_pit_lap"], in_force["next_compound"])
        fits = [o for o in options if o["stops"] and o["stops"][0] == first]
        if fits:
            in_force_delta = round(min(o["time"] for o in fits) - best, 1)
    elif in_force and in_force.get("next_compound") == "NONE":
        in_force_delta = round(min((o["time"] for o in options if not o["stops"]), default=best + 99) - best, 1)

    box_now = min((o["time"] for o in options if o["stops"] and o["stops"][0][0] == lap), default=None)
    stay_out = min((o["time"] for o in options if not o["stops"] or o["stops"][0][0] > lap), default=None)
    return {
        "lap": lap,
        "remaining": remaining,
        "conditions": "DRYING" if drying else ("WET" if wet else "DRY"),
        "current": {"compound": current, "age": age, "wear": round(wear_now, 1),
                    "laps_to_cliff": round(max(0.0, current_to_cliff - age), 1)},
        "rule_satisfied": rule_ok([]),
        "car_tyre_factor": round(model.car_factor, 2),
        "assumptions": {c: {"deg": round(model.deg[c], 3), "wear": round(model.wear[c], 2),
                            "pace": round(model.offset[c], 2), "source": model.sources[c]}
                        for c in (choices + (current,) if current not in choices else choices)},
        "options": shown,
        "box_now_by_compound": box_now_by_compound,
        "in_force_delta": in_force_delta,
        "box_now_vs_stay_out": round(box_now - stay_out, 1) if box_now is not None and stay_out is not None else None,
    }
