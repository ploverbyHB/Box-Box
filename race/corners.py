"""
Corner-by-corner analysis from every car's telemetry. Corners are found from the racing line of
the fastest lap, then each car's laps are measured through them: time, braking point, minimum
speed, throttle point and exit speed. Corner numbers are Box-Box's own, counted from the line.
"""
import bisect
import math

NEUTRALISED = ("FULL", "VIRTUAL")

SAMPLE_M = 5.0
GRID_M = 5.0
# A lap counts when its samples cover the lap without holes
EDGE_M = 60.0
MAX_HOLE_M = 60.0
# Laps slower than this times the car's best are traffic, mistakes or cool-down laps
REPRESENTATIVE = 1.07
LAPS_KEPT = 6

# Corner detection on the racing line
HEADING_SPAN_M = 25.0
MIN_CURVATURE = 1 / 220.0
MIN_TURN_DEG = 20.0
MERGE_GAP_M = 30.0
# Corners whose slowest points are this close are one complex (a hairpin or a double apex)
MERGE_APEX_M = 45.0
# Measurement windows around a corner
BRAKE_LOOKBACK_M = 220.0
EXIT_M = 60.0
THROTTLE_SEARCH_M = 200.0
BRAKING = 20.0
FULL_THROTTLE = 90.0
SLOW_KPH, FAST_KPH = 120, 200


class LapTrace:
    __slots__ = ("idx", "lap", "time", "finished_at", "dist", "speed", "throttle", "brake", "gear", "x", "z")

    def __init__(self, idx, lap, time, finished_at, samples):
        self.idx, self.lap, self.time, self.finished_at = idx, lap, time, finished_at
        self.dist, self.speed, self.throttle, self.brake, self.gear, self.x, self.z = (list(c) for c in zip(*samples))

    def at(self, series: list, d: float) -> float:
        i = bisect.bisect_left(self.dist, d)
        if i <= 0:
            return series[0]
        if i >= len(self.dist):
            return series[-1]
        d0, d1 = self.dist[i - 1], self.dist[i]
        f = (d - d0) / (d1 - d0) if d1 > d0 else 0.0
        return series[i - 1] + (series[i] - series[i - 1]) * f


class _Recorder:
    __slots__ = ("lap", "samples", "spoiled")

    def __init__(self, lap):
        self.lap, self.samples, self.spoiled = lap, [], False


class Corner:
    __slots__ = ("n", "start", "end", "apex", "turn", "min_speed")

    def __init__(self, n, start, end, apex, turn, min_speed):
        self.n, self.start, self.end, self.apex, self.turn, self.min_speed = n, start, end, apex, turn, min_speed

    def kind(self) -> str:
        if self.min_speed < SLOW_KPH:
            return "slow"
        return "medium" if self.min_speed < FAST_KPH else "fast"


class CornerAnalysis:
    def __init__(self):
        self.reset(None, 0)

    def reset(self, track_id, length: float):
        self.track_id, self.length = track_id, float(length or 0)
        self.recorders = {}
        self.laps = {}
        self.names = {}
        self.version = 0
        self._corners = None
        self._corners_ref = None

    def update(self, frame: dict, safety_car: str):
        track = frame.get("track") or {}
        if track.get("id") != self.track_id or (track.get("trackLength") and not self.length):
            self.reset(track.get("id"), track.get("trackLength") or 0)
        self.now = float(frame.get("sessionTime") or 0.0)
        for car in frame.get("cars") or []:
            self._sample(car, safety_car)

    def rewind(self, t: float):
        """A flashback: laps finished after t never happened, and every lap in progress is now a mix of two runs."""
        for idx, laps in self.laps.items():
            laps[:] = [l for l in laps if l.finished_at <= t]
        for rec in self.recorders.values():
            rec.spoiled = True
        self.version += 1

    def _sample(self, car: dict, safety_car: str):
        idx = car.get("idx")
        lap = car.get("currentLap") or 0
        rec = self.recorders.get(idx)
        if rec is None or lap != rec.lap:
            if rec is not None and lap == rec.lap + 1:
                self._finish(idx, rec, float(car.get("lastLapTime") or 0.0))
            rec = self.recorders[idx] = _Recorder(lap)
        self.names[idx] = car.get("name") or self.names.get(idx, f"#{idx}")
        if (car.get("pitStatus") or "NONE") != "NONE" or safety_car in NEUTRALISED:
            rec.spoiled = True
        d = car.get("lapDist")
        if d is None or d < 0 or (rec.samples and d - rec.samples[-1][0] < SAMPLE_M):
            return
        rec.samples.append((
            float(d), float(car.get("speed") or 0), float(car.get("throttle") or 0), float(car.get("brake") or 0),
            int(car.get("gear") or 0), float(car.get("x") or 0), float(car.get("z") or 0),
        ))

    def _finish(self, idx, rec: _Recorder, lap_time: float):
        s = rec.samples
        if rec.spoiled or lap_time <= 0 or len(s) < 20 or not self.length:
            return
        if s[0][0] > EDGE_M or s[-1][0] < self.length - EDGE_M:
            return
        if any(b[0] - a[0] > MAX_HOLE_M for a, b in zip(s, s[1:])):
            return
        laps = self.laps.setdefault(idx, [])
        laps.append(LapTrace(idx, rec.lap, lap_time, getattr(self, "now", 0.0), s))
        del laps[:-LAPS_KEPT]
        self.version += 1

    def best(self, idx):
        laps = self.laps.get(idx)
        return min(laps, key=lambda l: l.time) if laps else None

    def reference(self, exclude=None):
        bests = [self.best(i) for i in self.laps if i != exclude]
        bests = [b for b in bests if b]
        return min(bests, key=lambda l: l.time) if bests else None

    def corners(self) -> list:
        ref = self.reference()
        if ref is None:
            return []
        if self._corners is not None and self._corners_ref is ref:
            return self._corners
        self._corners, self._corners_ref = _find_corners(ref, self.length), ref
        return self._corners

    def labels(self) -> list:
        return [{"n": c.n, "dist": round(c.apex, 1)} for c in self.corners()]

    def report(self, player_idx: int) -> str:
        """Text for the engineer: the driver against the fastest lap and the field, and every opponent's weak corners."""
        corners = self.corners()
        if not corners:
            return "CORNERS: no complete clean lap from any car yet."
        bests = {i: self.best(i) for i in self.laps}
        bests = {i: b for i, b in bests.items() if b}
        measured = {i: [_measure(b, c, corners, self.length) for c in corners] for i, b in bests.items()}
        field_best = [min(m[k]["time"] for m in measured.values()) for k in range(len(corners))]
        mine = bests.get(player_idx)
        ref = self.reference(exclude=player_idx)
        lines = [
            "CORNERS (numbered by Box-Box from the racing line; shown on the dashboard map). Each car's best clean lap. "
            "brake = metres before the apex the braking starts; full throttle = where full throttle returns, measured from the apex; "
            "speeds in km/h, 'you X vs Y' compares you with the reference lap."
        ]
        if mine is None or ref is None:
            lines.append("  Your lap: none complete and clean yet." if mine is None else "  No other car has a clean lap yet.")
        else:
            lines.append(
                f"  Yours: L{mine.lap} {_t(mine.time)} | reference: {self.names.get(ref.idx)} L{ref.lap} {_t(ref.time)} "
                f"({mine.time - ref.time:+.3f}s)"
            )
            ref_m = measured[ref.idx]
            mine_m = measured[player_idx]
            for k, c in enumerate(corners):
                me, rf = mine_m[k], ref_m[k]
                rank = 1 + sum(1 for i, m in measured.items() if i != player_idx and m[k]["time"] < me["time"])
                lines.append(
                    f"  C{c.n} {c.kind()} ({c.turn:.0f} deg, ref min {rf['min_speed']:.0f} km/h) at {c.apex:.0f} m: "
                    f"you {me['time'] - rf['time']:+.3f}s, {_ordinal(rank)} of {len(measured)} | "
                    f"brake {_m(me['brake'])} vs {_m(rf['brake'])} | min {me['min_speed']:.0f} vs {rf['min_speed']:.0f} | "
                    f"full throttle {_throttle(me['throttle'])} vs {_throttle(rf['throttle'])} | "
                    f"exit {me['exit']:.0f} vs {rf['exit']:.0f} | gear {me['gear']} vs {rf['gear']} | {_diagnose(me, rf)}"
                )
            deltas = sorted(((mine_m[k]["time"] - field_best[k], corners[k].n) for k in range(len(corners))), reverse=True)
            lines.append("  Your biggest losses to the best in the field: "
                         + ", ".join(f"C{n} {d:+.3f}s" for d, n in deltas[:3] if d > 0.005))
            strong = [n for d, n in sorted(deltas)[:3] if d < 0.02]
            if strong:
                lines.append("  Your strongest corners: " + ", ".join(f"C{n}" for n in strong))
            last = self.laps[player_idx][-1]
            if last is not mine and last.time <= mine.time * REPRESENTATIVE:
                last_m = [_measure(last, c, corners, self.length) for c in corners]
                worst = sorted(((last_m[k]["time"] - mine_m[k]["time"], corners[k].n) for k in range(len(corners))), reverse=True)
                lines.append(f"  Last clean lap L{last.lap} {_t(last.time)} lost most against your best at: "
                             + ", ".join(f"C{n} {d:+.3f}s" for d, n in worst[:3] if d > 0.01))
        lines.append("OPPONENTS (weakest corners against the best in the field; where you are quicker than them):")
        for i, m in sorted(measured.items(), key=lambda kv: bests[kv[0]].time):
            if i == player_idx:
                continue
            weak = sorted(((m[k]["time"] - field_best[k], corners[k].n) for k in range(len(corners))), reverse=True)[:2]
            text = f"  {self.names.get(i)} {_t(bests[i].time)}: weak " + ", ".join(f"C{n} {d:+.3f}s" for d, n in weak)
            if player_idx in measured:
                faster = sorted(((m[k]["time"] - measured[player_idx][k]["time"], corners[k].n) for k in range(len(corners))),
                                reverse=True)
                gains = [f"C{n} {d:.3f}s" for d, n in faster[:3] if d > 0.02]
                text += " | you quicker at " + (", ".join(gains) if gains else "no corner")
            lines.append(text)
        return "\n".join(lines)


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _find_corners(ref: LapTrace, length: float) -> list:
    grid = [i * GRID_M for i in range(int(length / GRID_M))]
    xs = [ref.at(ref.x, d) for d in grid]
    zs = [ref.at(ref.z, d) for d in grid]
    span = max(1, int(HEADING_SPAN_M / GRID_M))
    n = len(grid)
    heading = [math.atan2(zs[min(n - 1, i + span)] - zs[max(0, i - span)], xs[min(n - 1, i + span)] - xs[max(0, i - span)])
               for i in range(n)]
    curv = [0.0] + [_wrap(heading[i] - heading[i - 1]) / GRID_M for i in range(1, n)]
    curv = [sum(curv[max(0, i - 2):i + 3]) / len(curv[max(0, i - 2):i + 3]) for i in range(n)]

    runs = []
    i = 0
    while i < n:
        if abs(curv[i]) < MIN_CURVATURE:
            i += 1
            continue
        sign = 1 if curv[i] > 0 else -1
        j = i
        while j < n and curv[j] * sign >= MIN_CURVATURE:
            j += 1
        runs.append([i, j - 1, sign])
        i = j
    merged = []
    for run in runs:
        if merged and run[2] == merged[-1][2] and (run[0] - merged[-1][1]) * GRID_M <= MERGE_GAP_M:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    found = []
    for a, b, _ in merged:
        turn = abs(sum(curv[a:b + 1]) * GRID_M)
        if math.degrees(turn) < MIN_TURN_DEG:
            continue
        window = grid[max(0, a - 4):min(n, b + 5)]
        apex = min(window, key=lambda d: ref.at(ref.speed, d))
        if found and apex - found[-1][2] < MERGE_APEX_M:
            lo, _, prev_apex, prev_turn = found[-1]
            apex = min((prev_apex, apex), key=lambda d: ref.at(ref.speed, d))
            found[-1] = (lo, grid[b], apex, prev_turn + math.degrees(turn))
        else:
            found.append((grid[a], grid[b], apex, math.degrees(turn)))
    return [Corner(k + 1, lo, hi, apex, turn, ref.at(ref.speed, apex)) for k, (lo, hi, apex, turn) in enumerate(found)]


def _measure(trace: LapTrace, c: Corner, corners: list, length: float) -> dict:
    k = c.n - 1
    prev_end = corners[k - 1].end if k > 0 else 0.0
    next_start = corners[k + 1].start if k + 1 < len(corners) else length
    w0 = max(prev_end, c.start - BRAKE_LOOKBACK_M)
    w1 = min(next_start, c.end + EXIT_M)
    steps = max(2, int((w1 - w0) / GRID_M))
    ds = (w1 - w0) / steps
    pts = [w0 + i * ds for i in range(steps + 1)]
    speeds = [max(trace.at(trace.speed, d), 5.0) for d in pts]
    time = sum(ds / ((speeds[i] + speeds[i + 1]) / 2 / 3.6) for i in range(steps))

    lo, hi = c.start - 20, c.end + 20
    inside = [d for d in pts if lo <= d <= hi] or [c.apex]
    d_min = min(inside, key=lambda d: trace.at(trace.speed, d))
    brake = next((c.apex - d for d in pts if d <= c.apex and trace.at(trace.brake, d) >= BRAKING), None)
    lifted = any(trace.at(trace.throttle, d) < FULL_THROTTLE for d in inside)
    throttle = None
    if lifted:
        reach = min(next_start, c.end + THROTTLE_SEARCH_M)
        d = d_min
        while d <= reach and trace.at(trace.throttle, d) < FULL_THROTTLE:
            d += GRID_M
        throttle = d - c.apex if d <= reach else None
    return {
        "time": time,
        "brake": brake,
        "min_speed": trace.at(trace.speed, d_min),
        "throttle": throttle if lifted else "flat",
        "exit": trace.at(trace.speed, w1),
        "gear": int(round(trace.at(trace.gear, d_min))),
    }


def _m(v) -> str:
    if v is None:
        return "none"
    if v == "flat":
        return "flat"
    return f"{v:.0f}m"


def _throttle(v) -> str:
    if v is None:
        return "not reached"
    if v == "flat":
        return "flat"
    return f"{v:.0f}m after" if v >= 0 else f"{-v:.0f}m before"


def _diagnose(me: dict, rf: dict) -> str:
    """The most likely reasons the driver loses or gains time against the reference at one corner."""
    notes = []
    if isinstance(me["brake"], (int, float)) and isinstance(rf["brake"], (int, float)):
        gap = me["brake"] - rf["brake"]
        if gap > 10:
            notes.append(f"brakes {gap:.0f}m earlier")
        elif gap < -10:
            notes.append(f"brakes {-gap:.0f}m later")
    elif me["brake"] is not None and rf["brake"] is None:
        notes.append("brakes where the reference does not")
    apex_gap = rf["min_speed"] - me["min_speed"]
    if apex_gap > 3:
        notes.append(f"{apex_gap:.0f} km/h slower at the apex, which carries onto the exit")
    elif apex_gap < -3:
        notes.append(f"{-apex_gap:.0f} km/h faster at the apex")
    # Speed gained from the apex to the exit: a smaller gain at a similar apex speed is a traction problem
    drive_gap = (rf["exit"] - rf["min_speed"]) - (me["exit"] - me["min_speed"])
    if abs(apex_gap) <= 3 and drive_gap > 4:
        mine_t, ref_t = me["throttle"], rf["throttle"]
        early = isinstance(mine_t, (int, float)) and isinstance(ref_t, (int, float)) and ref_t - mine_t > 15
        notes.append(f"gains {drive_gap:.0f} km/h less on the exit" + (": full throttle too early, likely wheelspin" if early else ""))
    elif isinstance(me["throttle"], (int, float)) and isinstance(rf["throttle"], (int, float)) and me["throttle"] - rf["throttle"] > 15:
        notes.append(f"full throttle {me['throttle'] - rf['throttle']:.0f}m later")
    if me["throttle"] not in (None, "flat") and rf["throttle"] == "flat":
        notes.append("lifts where the reference is flat")
    return "; ".join(notes) or "no single clear difference"


def _t(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
