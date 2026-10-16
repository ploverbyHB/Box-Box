"""
Turns race state, pit maths, the stop planner and the engineer's memory into the text the model reads.
"""
from engine_models import evaluate_tire

from . import pit, planner
from .pace import field_compound_stats, recent_pace, stint_pace
from .state import WET_TYRES


def _t(seconds) -> str:
    if not seconds:
        return "-"
    m, s = divmod(float(seconds), 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def _gap(x) -> str:
    return f"{x:+.1f}s" if isinstance(x, (int, float)) else "-"


def _tyre(car: dict) -> str:
    visual, actual = car.get("visualCompound") or "", car.get("compound") or ""
    label = f"{visual}({actual})" if visual and actual and visual != actual else (visual or actual or "?")
    return f"{label} {car.get('tyreAge', car.get('stintLaps', 0))}L"


def _race_header(state) -> list:
    t = state.track
    total = state.total_laps()
    remaining = state.laps_remaining()
    lines = [
        f"RACE: {t.get('name') or '?'}, lap {state.lap}/{total or '?'}"
        + (f" ({remaining} to go incl. this one)" if remaining is not None else " (race length unknown)")
        + f", session {t.get('sessionType') or '?'}",
        f"TRACK: {t.get('weather') or '?'}, track {t.get('trackTemp', '?')}C, air {t.get('airTemp', '?')}C, "
        f"safety car: {state.safety_car}",
    ]
    forecast = t.get("rainForecast") or []
    if forecast:
        lines.append("FORECAST: " + ", ".join(f"{s['rain']}% rain at +{s['minutes']}min" for s in forecast[:6]))
    window = t.get("pitWindow") or {}
    if window.get("idealLap") or window.get("rejoinPosition"):
        lines.append(
            f"GAME PIT WINDOW: ideal L{window.get('idealLap')}, latest L{window.get('latestLap')}, "
            f"game predicts rejoin P{window.get('rejoinPosition')}"
        )
    return lines


def _player_block(state) -> list:
    p = state.player()
    h = state.player_history()
    if not p or not h:
        return ["PLAYER: no data yet"]
    tyre = evaluate_tire(p)
    pace = stint_pace(h)
    wear = p.get("tireWear") or {}
    last = [l for l in h.lap_list()][-5:]
    stints = " -> ".join(
        f"{s.visual or s.compound} L{s.start_lap}-{s.end_lap or 'now'}" for s in h.stints
    )
    lines = [
        f"YOU: {p.get('name')}{' (' + p['team'] + ')' if p.get('team') else ''} P{p.get('position')} "
        f"(started P{p.get('gridPosition') or '?'}), "
        f"{_tyre(p)}, stops {p.get('numPitStops', 0)}, stints: {stints}"
        + (" | IN THE PIT LANE NOW, stop in progress" if (p.get("pitStatus") or "NONE") != "NONE" else ""),
        "  tyre wear % " + " ".join(f"{k}{v:.0f}" for k, v in wear.items())
        + f" | wear rate {pace['wear_rate'] or '?'}%/lap | model health {tyre.health_score:.0f}%",
        "  last laps: " + (" ".join(
            f"L{l.lap} {_t(l.time)}{'(pit)' if l.pit else ''}{'(SC)' if l.neutralised else ''}" for l in last
        ) or "none yet")
        + f" | stint deg {pace['deg'] if pace['deg'] is not None else '?'} s/lap over {pace['laps']} clean laps",
        f"  fuel margin {p.get('fuelRemainingLaps', '?')} laps, ERS {p.get('ersBattery', '?')}%, "
        f"front wing dmg {p.get('damage', 0)}%, body dmg {p.get('bodyDamage', 0)}%, tyre dmg {p.get('tyreDamage', 0)}%, "
        f"penalties {p.get('penaltiesSec', 0)}s, flag {p.get('fiaFlag', '?')}",
    ]
    return lines


def _neighbours(state) -> list:
    p = state.player()
    if not p:
        return []
    by_pos = {c.get("position"): c for c in state.cars if c.get("position")}
    mine = recent_pace(state.player_history())
    lines = []
    for label, pos, gap_key in (("AHEAD", p.get("position", 0) - 1, "gapToAhead"),
                                ("BEHIND", p.get("position", 0) + 1, "gapToBehind")):
        car = by_pos.get(pos)
        if not car:
            continue
        theirs = recent_pace(state.histories.get(car.get("idx")))
        delta = f"{theirs - mine:+.2f} s/lap vs you" if theirs and mine else "pace unknown"
        lines.append(
            f"{label}: {car.get('name')} P{pos}, gap {p.get(gap_key, 0):.1f}s, {_tyre(car)}, "
            f"stops {car.get('numPitStops', 0)}, recent pace {delta}"
        )
    return lines


def _teammate(state) -> list:
    mate = state.teammate()
    p = state.player()
    if not mate or not p:
        return []
    h = state.histories.get(mate.get("idx"))
    pace = stint_pace(h) if h else {"wear_rate": None}
    wear_values = list((mate.get("tireWear") or {}).values())
    wear = sum(wear_values) / len(wear_values) if wear_values else None
    to_cliff = (
        f", ~{max(0.0, (planner.CLIFF_WEAR - wear) / pace['wear_rate']):.0f} laps to the wear cliff"
        if wear is not None and pace["wear_rate"] else ""
    )
    behind_by = (p.get("gapToLeader") or 0.0) - (mate.get("gapToLeader") or 0.0)
    where = f"{behind_by:.1f}s ahead of you" if behind_by >= 0 else f"{-behind_by:.1f}s behind you"
    status = " | IN THE PIT LANE NOW" if (mate.get("pitStatus") or "NONE") != "NONE" else ""
    return [
        f"TEAMMATE (shares your pit box): {mate.get('name')} P{mate.get('position')}, {where}, {_tyre(mate)}"
        + (f", wear {wear:.0f}%" if wear is not None else "") + to_cliff
        + f", stops {mate.get('numPitStops', 0)}{status}"
    ]


def _still_to_stop(state, car: dict) -> bool:
    """A car that must still pit: wrong tyres for the conditions, or two dry compounds not yet used."""
    tyre = (car.get("visualCompound") or car.get("compound") or "").upper()
    if state.is_wet() != (tyre in WET_TYRES):
        return True
    h = state.histories.get(car.get("idx"))
    used = h.compounds_used() if h else [tyre]
    return not state.is_wet() and not any(c in WET_TYRES for c in used) and len(set(used)) < 2


def _loss_now(state) -> tuple[float, float, float]:
    track_id = state.track.get("id", -1)
    queue = pit.double_stack_delay(state.player(), state.teammate())
    return pit.pit_loss(track_id, state.safety_car) + queue, pit.pit_loss(track_id), queue


def _pit_block(state, plan_result) -> list:
    now, green, queue = _loss_now(state)
    r = pit.rejoin(state.cars, state.player_idx, now)
    lines = [f"PIT LOSS: {now}s if you box this lap" + (f" (green flag {green}s)" if now != green else "")]
    if queue:
        lines.append(f"DOUBLE STACK: your teammate is in the box ahead of you; boxing this lap queues ~{queue}s (included above)")
    if state.safety_car in ("FULL", "VIRTUAL"):
        lines.append(
            f"NEUTRALISED STOP: boxing under the {'safety car' if state.safety_car == 'FULL' else 'VSC'} costs {now - queue:.1f}s "
            f"instead of {green}s, saving {green - (now - queue):.1f}s; most rivals due a stop will take it"
        )
    if r:
        player = state.player()
        passed = [
            c for c in state.cars
            if c.get("idx") != state.player_idx and c.get("position")
            and (c.get("position") or 99) > (player.get("position") or 0)
            and (c.get("position") or 99) <= r["position"]
        ]
        must_stop = sum(1 for c in passed if _still_to_stop(state, c))
        around = []
        if r["ahead"]:
            around.append(f"{r['gap_ahead']}s behind {r['ahead']}")
        if r["behind"]:
            around.append(f"{r['gap_behind']}s ahead of {r['behind']}")
        lines.append(
            f"BOX THIS LAP -> rejoin P{r['position']} ({r['positions_lost']:+d} places)"
            + (", " + ", ".join(around) if around else "")
            + (" | TRAFFIC" if r["traffic"] else "") + (" | EXPOSED to the car behind" if r["exposed"] else "")
        )
        if passed:
            lines.append(f"  of the {len(passed)} cars you would drop behind, {must_stop} still have to stop themselves")
    field = {k: v for k, v in field_compound_stats(state.histories.values()).items() if k and v["last_lap"]}
    if field:
        lines.append("FIELD PACE BY TYRE (median last lap): " + " | ".join(
            f"{k} {_t(v['last_lap'])} ({v['cars']} cars)" for k, v in sorted(field.items(), key=lambda kv: kv[1]["last_lap"])
        ))
    if plan_result:
        c = plan_result["current"]
        lines.append(
            f"STRATEGY TOOL ({plan_result['conditions']} conditions assumed for the rest of the race): "
            f"current {c['compound']} {c['age']}L, wear {c['wear']}%, ~{c['laps_to_cliff']} laps to the wear cliff; "
            f"two-compound rule {'satisfied' if plan_result['rule_satisfied'] else 'NOT yet satisfied'}; "
            f"your car wears tyres x{plan_result['car_tyre_factor']} the field"
        )
        for i, o in enumerate(plan_result["options"], 1):
            lines.append(f"  {i}. {o['plan']}: {'+' if o['delta'] else ''}{o['delta']}s")
        if plan_result["in_force_delta"] is not None:
            lines.append(f"  your plan in force: +{plan_result['in_force_delta']}s vs the best option")
        if plan_result["box_now_by_compound"]:
            lines.append("  if you box THIS LAP, best plan for each tyre fitted now:")
            for b in plan_result["box_now_by_compound"]:
                lines.append(f"    {b['compound']}: {b['plan']}: +{b['delta']}s")
        if plan_result["box_now_vs_stay_out"] is not None:
            d = plan_result["box_now_vs_stay_out"]
            lines.append(f"  boxing this lap vs staying out: {'+' if d > 0 else ''}{d}s ({'slower' if d > 0 else 'faster'})")
        lines.append("  model inputs: " + "; ".join(
            f"{c} deg {a['deg']} wear {a['wear']}%/L pace {a['pace']:+} ({a['source']})"
            for c, a in plan_result["assumptions"].items()
        ))
    else:
        lines.append("STRATEGY TOOL: unavailable (race length or player data unknown)")
    return lines


def _net_order(state) -> list:
    """Where everyone ends up once the cars that still owe a stop have made it, at the green-flag pit loss."""
    player = state.player()
    if not player or player.get("gapToLeader") is None:
        return []
    if (player.get("pitStatus") or "NONE") != "NONE":
        return ["NET ORDER ONCE EVERYONE HAS STOPPED: unknown while you are in the pit lane; ask again after the stop"]
    green = pit.pit_loss(state.track.get("id", -1))
    net, owing = [], []
    for car in state.cars:
        if not car.get("position") or (car.get("resultStatus") or "ACTIVE") not in ("ACTIVE", "FINISHED") or car.get("gapToLeader") is None:
            continue
        owes = _still_to_stop(state, car)
        if owes:
            owing.append(car)
        net.append((car.get("gapToLeader") + (green if owes else 0.0), car))
    net.sort(key=lambda x: x[0])
    me = next((i for i, (_, c) in enumerate(net) if c.get("idx") == state.player_idx), None)
    if me is None:
        return []
    mine = net[me][0]
    ahead_now = [c for c in owing if (c.get("position") or 99) < (player.get("position") or 0)]
    line = (f"NET ORDER ONCE EVERYONE HAS STOPPED (at the current gaps, {green}s green-flag pit loss added to each car that still has to stop"
            + (", you included" if any(c.get("idx") == state.player_idx for c in owing) else "")
            + f"; cars that stop later will be on fresher tyres): you P{me + 1} (now P{player.get('position')})")
    if me > 0:
        line += f", {net[me - 1][1].get('name')} {mine - net[me - 1][0]:.1f}s ahead of you then"
    if me + 1 < len(net):
        line += f", {net[me + 1][1].get('name')} {net[me + 1][0] - mine:.1f}s behind"
    line += (f"; {len(ahead_now)} of the cars ahead of you now still have to stop"
             + (f" ({', '.join(c.get('name') or '?' for c in sorted(ahead_now, key=lambda c: c.get('position')))})" if ahead_now else ""))
    return [line]


def _field(state) -> list:
    lines = ["FIELD (pos name gap-to-leader tyre stops last-lap status):"]
    for car in sorted(state.cars, key=lambda c: c.get("position") or 99):
        if not car.get("position"):
            continue
        status = car.get("resultStatus") or "ACTIVE"
        flags = []
        if car.get("pitStatus") not in (None, "NONE"):
            flags.append("IN PITS")
        if status != "ACTIVE":
            flags.append(status)
        if car.get("idx") == state.player_idx:
            flags.append("YOU")
        elif state.teammate() and car.get("idx") == state.teammate().get("idx"):
            flags.append("TEAMMATE")
        lines.append(
            f"  P{car['position']} {car.get('name')} +{car.get('gapToLeader', 0):.1f} {_tyre(car)} "
            f"s{car.get('numPitStops', 0)} {_t(car.get('lastLapTime'))} {' '.join(flags)}".rstrip()
        )
    return lines


def _race_log(state) -> list:
    if not state.log:
        return ["RACE LOG: nothing yet"]
    return ["RACE LOG:"] + [f"  L{e['lap']}: {e['text']}" for e in state.log]


def strategy_context(state, memory, reasons) -> str:
    now, green, _ = _loss_now(state)
    plan_result = planner.plan(state, now, green, memory.plan)
    sections = [
        "WHY YOU ARE CALLED: " + "; ".join(f"{r.code} ({r.detail})" for r in reasons),
        *_race_header(state),
        *_player_block(state),
        *_neighbours(state),
        *_teammate(state),
        *_pit_block(state, plan_result),
        *_net_order(state),
        *_field(state),
        *_race_log(state),
        "MEMORY:",
        memory.describe(),
    ]
    return "\n".join(sections)


def radio_context(state, memory, message: str, corners: str = "") -> str:
    if state.is_race():
        now, green, _ = _loss_now(state)
        strategy = _pit_block(state, planner.plan(state, now, green, memory.plan)) + _net_order(state)
    else:
        strategy = [f"{state.track.get('sessionType') or 'This session'} is not a race: talk pace, tyres and the car, not race strategy."]
    sections = [
        f'DRIVER SAYS: "{message}"',
        *_race_header(state),
        *_player_block(state),
        *_neighbours(state),
        *_teammate(state),
        *strategy,
        *([corners] if corners else []),
        "RECENT RACE LOG:",
        *[f"  L{e['lap']}: {e['text']}" for e in state.log[-12:]],
        "MEMORY:",
        memory.describe(),
    ]
    return "\n".join(sections)
