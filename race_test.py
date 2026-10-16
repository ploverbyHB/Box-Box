"""
Runs a simulated race headless with the real engineer in the loop and prints what it decided.

  python race_test.py --scenario safety-car
  python race_test.py --scenario rain --say "3:I'd rather one-stop if we can"
  python race_test.py --scenario damage --baseline     # fixed one-stop, no engineer, for comparison

The race keeps running while each call is answered (as it would in the game), so a slow
engineer can miss a pit entry. The player's car only pits when the engineer calls it in.
"""
import argparse
import json
import os
import time
from pathlib import Path

from simulator import SCENARIOS, RaceSim
from race import EngineerMemory, RaceState, Trigger, radio_context, strategy_context

STEP_S = 0.5


def run(args) -> dict:
    if not args.baseline:
        from analyzer import radio_call, strategy_call
    sim = RaceSim(scenario=args.scenario, laps=args.laps, seed=args.seed)
    state = RaceState()
    memory = EngineerMemory()
    radio = {}
    for item in args.say:
        lap, _, text = item.partition(":")
        radio.setdefault(int(lap), []).append(text)

    calls, laps, pending = [], [], []
    baseline_lap = round(args.laps * 0.45)
    baseline_boxed = False
    box_lap = None

    def advance(seconds: float):
        nonlocal pending
        steps = max(1, int(seconds / STEP_S))
        for _ in range(steps):
            if sim.finished:
                return
            sim.step(STEP_S)
            frame = sim.frame()
            triggers, _ = state.update(frame)
            pending += [t for t in triggers if not any(p.code == t.code and p.detail == t.detail for p in pending)]
            for t in triggers:
                if t.code == "LAP":
                    laps.append(lap_row(state))

    while not sim.finished:
        advance(STEP_S)
        if args.baseline:
            if state.lap == baseline_lap and not baseline_boxed:
                sim.command_pit("HARD")
                baseline_boxed = True
            pending.clear()
            continue

        for text in radio.pop(state.lap, []):
            started = time.time()
            reply = radio_call(radio_context(state, memory, text, state.corners.report(state.player_idx)))
            took = time.time() - started
            memory.record_radio(state.lap, text, reply["voice"])
            calls.append({"lap": state.lap, "radio": text, "voice": reply["voice"], "remember": reply["remember"],
                          "latency": round(took, 1)})
            print(f"  L{state.lap:>2} RADIO   driver: {text!r} -> {reply['voice']!r}"
                  + (f" [remember: {reply['remember']}]" if reply["remember"] else "") + f" ({took:.1f}s)")
            if memory.add_preference(reply["remember"]):
                pending.append(Trigger("DRIVER", f'{reply["remember"]}; you already answered on the radio ("{reply["voice"]}"): the plan must match that answer, '
                                                 f'and voice stays "" unless the driver must act now'))
            advance(took * args.latency_scale)

        if not any(t.urgent or t.code == "LAP" for t in pending):
            continue
        reasons, pending = pending, []
        lap = state.lap
        started = time.time()
        context = strategy_context(state, memory, reasons)
        result = strategy_call(context)
        took = time.time() - started
        advance(took * args.latency_scale)
        if ((state.player() or {}).get("pitStatus") or "NONE") != "NONE":
            result["box_this_lap"] = False
        memory.record_call(lap, reasons, result)
        plan = result["plan"] or {}
        withdrawn = False
        if result["box_this_lap"]:
            sim.command_pit(plan.get("next_compound") or "MEDIUM")
            box_lap = lap
        elif box_lap == lap:
            withdrawn = sim.cancel_pit()
        calls.append({
            "lap": lap, "reasons": [f"{r.code}: {r.detail}" for r in reasons], "voice": result["voice"],
            "box": result["box_this_lap"], "compound": plan.get("next_compound"), "plan": plan.get("summary"),
            "next_pit_lap": plan.get("next_pit_lap"), "stops_remaining": plan.get("stops_remaining"),
            "player_stops": (state.player() or {}).get("numPitStops"),
            "advice": result["advice"], "note": result["note"], "latency": round(took, 1), "withdrawn": withdrawn,
            "context": context,
        })
        tag = f"BOX->{plan.get('next_compound')}" if result["box_this_lap"] else ("STAY OUT" if withdrawn else "")
        print(f"  L{lap:>2} {tag:<12} [{','.join(r.code for r in reasons)}] "
              f"{result['voice'] or '(quiet)'} | plan: {plan.get('summary', '')} ({took:.1f}s)")

    results = sim.results()
    player = next(r for r in results if r["idx"] == sim.player_idx)
    return {"scenario": args.scenario, "seed": args.seed, "baseline": args.baseline,
            "provider": os.getenv("AI_PROVIDER", "deepseek"), "player": player,
            "calls": calls, "laps": laps, "log": state.log, "results": results}


def lap_row(state) -> dict:
    p = state.player() or {}
    return {
        "lap": state.lap - 1, "pos": p.get("position"), "tyre": p.get("visualCompound") or p.get("compound"),
        "age": p.get("tyreAge"), "last": p.get("lastLapTime"), "ahead": p.get("gapToAhead"),
        "behind": p.get("gapToBehind"), "sc": state.safety_car, "weather": state.track.get("weather"),
        "stops": p.get("numPitStops"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
    ap.add_argument("--laps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--baseline", action="store_true", help="no engineer: fixed one-stop for HARD at 45%% distance")
    ap.add_argument("--say", action="append", default=[], metavar="LAP:TEXT", help="driver radio message on a lap")
    ap.add_argument("--latency-scale", type=float, default=1.0, help="sim seconds that pass per second of AI latency")
    ap.add_argument("--out", help="write the full run as JSON here")
    args = ap.parse_args()

    print(f"=== {args.scenario}: {SCENARIOS[args.scenario]} ({args.laps} laps, seed {args.seed}"
          f"{', BASELINE' if args.baseline else ''}) ===")
    out = run(args)
    print("\n lap pos tyre    age  last     ahead behind sc       weather")
    for r in out["laps"]:
        print(f" {r['lap']:>3} P{r['pos']:<2} {str(r['tyre']):<7} {r['age']!s:>3}  {r['last'] or 0:7.3f}  "
              f"{r['ahead'] or 0:5.1f} {r['behind'] or 0:6.1f} {r['sc']:<8} {r['weather']}")
    p = out["player"]
    print(f"\nFINISH P{p['position']} | stops {p.get('stops')} | tyres {p.get('compounds')} | time {p.get('total_time')}")
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
