"""
Pace from lap history: representative lap times, tyre degradation and wear rate per stint.
"""
from statistics import median

from engine_models import FUEL_BASE_CONSUMPTION, FUEL_S_PER_KG

# Laps slower than the stint median by more than this are traffic, mistakes or incidents
OUTLIER_S = 2.5
# Burning fuel makes each lap quicker; add it back so the slope measures the tyres alone
FUEL_GAIN_PER_LAP = FUEL_BASE_CONSUMPTION * FUEL_S_PER_KG


def _slope(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    var = sum((x - mx) ** 2 for x, _ in points)
    if var == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / var


def clean_laps(laps: list) -> list:
    """Racing laps only: no lap 1, pit laps, neutralised laps, or outliers."""
    usable = [l for l in laps if l.lap > 1 and not l.pit and not l.neutralised and l.time > 0]
    if len(usable) >= 3:
        mid = median(l.time for l in usable)
        usable = [l for l in usable if l.time - mid <= OUTLIER_S]
    return usable


def stint_pace(history) -> dict:
    """Pace of a car's current stint: last-three average, degradation and tyre wear per lap."""
    stint = history.current_stint()
    laps = [l for l in history.lap_list() if stint and l.lap >= stint.start_lap]
    clean = clean_laps(laps)
    recent = clean[-3:]
    return {
        "laps": len(clean),
        "avg3": round(sum(l.time for l in recent) / len(recent), 3) if recent else None,
        "best": round(min(l.time for l in clean), 3) if clean else None,
        "deg": _fuel_corrected(_slope([(l.tyre_age, l.time) for l in clean])),
        "wear_rate": _round(_slope([(l.tyre_age, l.wear) for l in laps if l.wear is not None and l.lap > 1])),
    }


def recent_pace(history, n: int = 3) -> float | None:
    clean = clean_laps(history.lap_list())[-n:]
    return round(sum(l.time for l in clean) / len(clean), 3) if clean else None


def field_compound_stats(histories) -> dict:
    """Median degradation and wear rate per tyre type across the field's current stints."""
    by_compound = {}
    for h in histories:
        stint = h.current_stint()
        if not stint:
            continue
        p = stint_pace(h)
        entry = by_compound.setdefault(stint.visual or stint.compound, {"deg": [], "wear_rate": [], "last": []})
        if p["deg"] is not None and p["laps"] >= 4:
            entry["deg"].append(p["deg"])
        if p["wear_rate"] is not None and p["wear_rate"] > 0:
            entry["wear_rate"].append(p["wear_rate"])
        last = h.last_lap()
        if last and not last.pit and last.time > 0:
            entry["last"].append(last.time)
    return {
        name: {
            "cars": len(v["last"]),
            "deg": _round(median(v["deg"])) if v["deg"] else None,
            "wear_rate": _round(median(v["wear_rate"])) if v["wear_rate"] else None,
            "last_lap": round(median(v["last"]), 3) if v["last"] else None,
        }
        for name, v in by_compound.items()
    }


def _fuel_corrected(slope: float | None) -> float | None:
    return _round(slope + FUEL_GAIN_PER_LAP) if slope is not None else None


def _round(x: float | None) -> float | None:
    return round(x, 3) if x is not None else None
