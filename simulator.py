"""
Race simulator — 22-car race with tyre wear, fuel, pit stops, safety cars, rain, damage and scripted scenarios.

Runs as a WebSocket telemetry source for the server (and obeys the engineer's box calls), or headless to
eyeball a scenario: python simulator.py --headless --scenario safety-car
"""
import argparse
import asyncio
import json
import math
import random
import time

import websockets

RECONNECT_DELAY = 3

TRACK_ID = 7
TRACK_NAME = "Silverstone"
TRACK_LENGTH = 5891  # meters
CHECKPOINT_M = 100  # timing loop spacing; gaps are measured at these loops like live timing
GRID_SPACING_M = 8.0
MAX_STEP_S = 0.25

BASE_LAP_S = 88.0
START_PENALTY_S = 2.5  # standing start on lap 1
LAP_NOISE_S = 0.15
FUEL_S_PER_KG = 0.03
FUEL_BURN_KG = 1.6
FUEL_MARGIN_KG = 2.0
CAUTION_FUEL_FACTOR = 0.5

PIT_LOSS_S = 20.5  # green: lane time loss including the stationary time
PIT_STATIONARY_S = 2.5
PIT_LOSS_FACTOR = {"NONE": 1.0, "FULL": 0.55, "VIRTUAL": 0.65, "FORMATION": 1.0}
WING_CHANGE_S = 6.0
FRESH_TYRE_BONUS_S = 0.4  # first two laps on a new set
UNDERCUT_OUT_LAP_BOOST_S = -1.5

SC_LAP_FACTOR = 1.4
VSC_LAP_FACTOR = 1.35
SC_GAP_S = 0.8
FOLLOW_GAP_S = 0.4
OVERTAKE_DELTA_S = 0.5  # pace advantage per lap needed to get past a car within FOLLOW_GAP_S

WING_DAMAGE_S_PER_PCT = 0.03
PUNCTURE_S = 20.0

WET_RAIN_RATE = 0.2  # wetness gained per lap at rainfall 1.0
WET_DRY_RATE = 0.08  # wetness lost per lap once the rain stops
WET_TRACK_S_PER_UNIT = 8.0  # everyone is slower on a wet track, whatever the tyre

# Weekend allocation: the visual compound and the actual C-number fitted for it
ALLOCATION = {"HARD": "C2", "MEDIUM": "C3", "SOFT": "C4", "INTER": "INTER", "WET": "WET"}
C_NUMBER_TO_VISUAL = {
  "C0": "HARD", "C1": "HARD", "C2": "HARD", "C3": "MEDIUM", "C4": "SOFT", "C5": "SOFT", "C6": "SOFT",
}
DRY = ("SOFT", "MEDIUM", "HARD")
PACE_OFFSET_S = {"SOFT": -0.6, "MEDIUM": 0.0, "HARD": 0.5, "INTER": 0.0, "WET": 0.0}
DEG_S_PER_LAP = {"SOFT": 0.10, "MEDIUM": 0.065, "HARD": 0.045, "INTER": 0.08, "WET": 0.06}
TYRE_LIFE_LAPS = {"SOFT": 14, "MEDIUM": 24, "HARD": 34, "INTER": 22, "WET": 28}
WEAR_AT_LIFE = 70.0
WHEEL_WEAR = {"FL": 1.06, "FR": 1.04, "RL": 0.95, "RR": 0.93}
TYRE_TEMP_TARGET = {"SOFT": 98.0, "MEDIUM": 95.0, "HARD": 92.0, "INTER": 82.0, "WET": 78.0}

UPSHIFT_SPEED = [0, 0, 32, 134, 167, 195, 236, 270, 304]
GEAR_RPM = [
  (3500, 13500), (4000, 13100), (7000, 12800), (8500, 12600),
  (9000, 12600), (9500, 11600), (9500, 11900), (9800, 11900), (9700, 12500),
]

SPEED_PROFILE = [
  275, 285, 290, 292, 290, 285, 270, 250, 225, 195,
  165, 135, 120, 118, 120, 125, 135, 150, 170, 195,
  220, 245, 270, 290, 308, 318, 324, 325, 323, 318,
  312, 306, 298, 290, 281, 275, 270, 268, 270, 275,
]
PROFILE_MEAN = sum(SPEED_PROFILE) / len(SPEED_PROFILE)

DRIVERS = [
  (1, "VER"), (11, "PER"), (16, "LEC"), (55, "SAI"),
  (44, "HAM"), (63, "RUS"), (4, "NOR"), (81, "PIA"),
  (14, "ALO"), (18, "STR"), (10, "GAS"), (31, "OCO"),
  (77, "BOT"), (24, "ZHO"), (22, "TSU"), (3, "RIC"),
  (20, "MAG"), (27, "HUL"), (2, "SAR"), (23, "ALB"),
  (5, "BEA"), (97, "VES"),
]

# F1 25 spec team ids; drivers in DRIVERS are listed in teammate pairs
TEAMS = {
  "VER": "Red Bull Racing", "PER": "Red Bull Racing", "LEC": "Ferrari", "SAI": "Ferrari",
  "HAM": "Mercedes", "RUS": "Mercedes", "NOR": "McLaren", "PIA": "McLaren",
  "ALO": "Aston Martin", "STR": "Aston Martin", "GAS": "Alpine", "OCO": "Alpine",
  "BOT": "Sauber", "ZHO": "Sauber", "TSU": "RB", "RIC": "RB",
  "MAG": "Haas", "HUL": "Haas", "SAR": "Williams", "ALB": "Williams",
  "BEA": "F1 Generic", "VES": "F1 Generic",
}
TEAM_IDS = {
  "Mercedes": 0, "Ferrari": 1, "Red Bull Racing": 2, "Williams": 3, "Aston Martin": 4, "Alpine": 5,
  "RB": 6, "Haas": 7, "McLaren": 8, "Sauber": 9, "F1 Generic": 41,
}
TEAM_COLOURS = {
  "Mercedes": "#27F4D2", "Ferrari": "#E8002D", "Red Bull Racing": "#3671C6", "McLaren": "#FF8000",
  "Aston Martin": "#229971", "Alpine": "#FF87BC", "Williams": "#64C4FF", "RB": "#6692FF",
  "Sauber": "#52E252", "Haas": "#B6BABD", "F1 Generic": "#FFFFFF",
}
DOUBLE_STACK_GAP_S = 1.0  # a queued car is released this long after its teammate leaves the box

ONE_STOP_PLANS = [("MEDIUM", "HARD"), ("SOFT", "HARD"), ("HARD", "MEDIUM"), ("MEDIUM", "SOFT")]
TWO_STOP_PLANS = [("SOFT", "MEDIUM", "SOFT"), ("MEDIUM", "HARD", "SOFT"), ("SOFT", "HARD", "MEDIUM"),
                  ("SOFT", "MEDIUM", "MEDIUM")]

SCENARIOS = {
  "normal": "Dry race, no incidents",
  "safety-car": "A retirement brings out a full safety car at ~40% distance for 3 laps",
  "vsc": "A retirement brings out a virtual safety car at ~35% distance for 2 laps",
  "rain": "Rain arrives at ~45% distance, heavy for a while, drying near the end",
  "damage": "The player loses 45% front wing in contact at ~30% distance",
  "puncture": "The player gets a puncture at ~55% distance",
  "high-deg": "The player's tyres degrade 1.8x faster than everyone else's",
  "undercut": "The car ahead of the player pits early for an undercut with a strong out-lap",
  "chaos": "High player degradation, a safety car at ~30% and rain at ~55%",
  "double-stack": "The player's teammate runs 1-2 s ahead on the front row and boxes on the lap the player's first stop is due",
}


def gear_from_speed(speed):
  if speed < 2:
    return 0
  for g in range(8, 0, -1):
    if speed >= UPSHIFT_SPEED[g]:
      return g
  return 1


def rpm_from_speed(speed, gear, rng):
  if gear == 0:
    return 3500 + rng.uniform(-100, 100)
  low = UPSHIFT_SPEED[gear]
  high = UPSHIFT_SPEED[gear + 1] if gear < 8 else UPSHIFT_SPEED[8] + 40
  rl, rh = GEAR_RPM[gear]
  if high <= low:
    return (rl + rh) / 2
  t = min(1.0, max(0.0, (speed - low) / (high - low)))
  return rl + t * (rh - rl)


def throttle_brake(speed, next_speed, rng):
  delta = next_speed - speed
  if delta > 0.5:
    return round(min(100, 50 + delta * 3 + rng.uniform(-3, 0)), 1), 0
  elif delta < -0.5:
    return round(max(0, min(30, (speed * 0.1) - (delta * -0.5))), 1), round(max(0, min(100, -delta * 1.8 + rng.uniform(-2, 2))), 1)
  else:
    return round(max(30, min(100, speed * 0.25)), 1), 0


def cliff_penalty(laps_over):
  if laps_over <= 0:
    return 0.0
  return 0.15 * laps_over + 0.06 * laps_over * laps_over


def wear_percent(eff_age, life):
  if eff_age <= life:
    return WEAR_AT_LIFE * eff_age / life
  over = eff_age - life
  return min(100.0, WEAR_AT_LIFE + 4.0 * over + 0.6 * over * over)


def weather_penalty(compound, wetness):
  if compound in DRY:
    return 28.0 * (wetness - 0.08) if wetness > 0.08 else 0.0
  if compound == "INTER":
    if wetness <= 0.25:
      return 0.8 + 3.2 * (1 - wetness / 0.25)
    if wetness <= 0.6:
      return 0.8
    return 0.8 + (wetness - 0.6) * 15.0
  if wetness <= 0.5:
    return 2.5 + 6.0 * (0.5 - wetness)
  return max(0.6, 2.5 - (wetness - 0.5) * 8.0)


def rain_label(rainfall, overcast):
  if rainfall >= 0.5:
    return "HeavyRain"
  if rainfall > 0:
    return "LightRain"
  return "Overcast" if overcast else "Clear"


def fmt_lap(seconds):
  if not seconds:
    return "--:--.---"
  m, s = divmod(seconds, 60)
  return f"{int(m)}:{s:06.3f}"


def to_visual(compound):
  c = (compound or "").strip().upper()
  if c in ALLOCATION:
    return c
  return C_NUMBER_TO_VISUAL.get(c, "MEDIUM")


class _Car:
  def __init__(self, idx, number, name, skill):
    self.idx = idx
    self.number = number
    self.name = name
    self.skill = skill
    self.grid = 0
    self.dist = 0.0
    self.speed = 0.0
    self.lap_t = BASE_LAP_S
    self.laps_done = 0
    self.lap_start_t = 0.0
    self.lap_noise = 0.0
    self.last_lap = 0.0
    self.best_lap = 0.0
    self.lap_times = []
    self.compound = "MEDIUM"
    self.tyre_laps = 0.0
    self.deg_mult = 1.0
    self.stints = []
    self.stint_start_lap = 0
    self.plan = []  # AI: [(pit at end of this lap, compound), ...]
    self.used_wet = False
    self.pit_pending = None
    self.team = TEAMS[name]
    self.team_id = TEAM_IDS[self.team]
    self.colour = TEAM_COLOURS[self.team]
    self.teammate = idx
    self.noise_sigma = LAP_NOISE_S
    self.pit_phase = None  # lane_in, queue, box, lane_out
    self.pit_end_t = 0.0  # sim time the current pit phase ends
    self.queue_from = 0.0
    self.queue_s = 0.0
    self.pit_box_s = 0.0
    self.pit_lane_out_s = 0.0
    self.pit_compound = None
    self.pit_unscheduled = False
    self.stops = 0
    self.last_stop_lap = 0
    self.out_lap = False
    self.out_lap_boost = 0.0
    self.next_out_lap_boost = 0.0
    self.fuel = 0.0
    self.wing = 0
    self.tyre_damage = 0
    self.puncture_wheel = "FL"
    self.body = 0
    self.status = "ACTIVE"
    self.finish_time = None
    self.cp = {}
    self.wear_jitter = 1.0
    self.temps = {"FL": 80.0, "FR": 80.0, "RL": 78.0, "RR": 78.0}
    self.ers = 100.0
    self.engine_temp = 105.0
    self.rain_threshold = 0.25
    self.dry_threshold = 0.15
    self.start_bonus = 0.0

  @property
  def in_pit(self):
    return self.pit_phase is not None

  @property
  def progress(self):
    return self.dist / TRACK_LENGTH


class RaceSim:
  def __init__(self, scenario="normal", laps=30, seed=1, player_idx=19):
    if scenario not in SCENARIOS:
      raise ValueError(f"Unknown scenario {scenario!r}. Available: {', '.join(SCENARIOS)}")
    self.scenario = scenario
    self.laps = int(laps)
    self.player_idx = player_idx
    self.rng = random.Random(seed)
    self.display_rng = random.Random(seed + 7919)
    self.session_uid = "%032x" % self.rng.getrandbits(128)
    self.t = 0.0
    self.finished = False
    self.chequered = False
    self.safety_car = "NONE"
    self.caution_end_lap = 0
    self.caution_returning_sent = False
    self.leader_laps = 0
    self.wetness = 0.0
    self.events = []
    self.log = []  # (sim time, text) for headless printing

    self.retire_at = None
    self.retire_reason = ""
    self.caution_type = None
    self.caution_laps = 0
    self.caution_deploy_t = None
    self.retired_idx = None
    self.rain_start = None
    self.rain_heavy_end = None
    self.max_wetness = 0.62
    self.damage_at = None
    self.puncture_at = None
    self.undercut_lap = None
    self.undercut_idx = None

    self._setup_cars()
    self._setup_scenario()

  # ---------- setup ----------

  def _setup_cars(self):
    n = len(DRIVERS)
    spread = [-1.0 + 2.0 * i / (n - 1) for i in range(n)]
    player_skill = spread[n // 2]
    others = [s for i, s in enumerate(spread) if i != n // 2]
    self.cars = []
    for idx, (number, name) in enumerate(DRIVERS):
      skill = player_skill if idx == self.player_idx else others.pop(0)
      car = _Car(idx, number, name, skill + self.rng.gauss(0, 0.08))
      car.wear_jitter = self.rng.uniform(0.96, 1.04)
      car.rain_threshold = self.rng.uniform(0.22, 0.32)
      car.dry_threshold = self.rng.uniform(0.10, 0.17)
      car.fuel = self.laps * FUEL_BURN_KG + FUEL_MARGIN_KG
      self.cars.append(car)

    by_team = {}
    for car in self.cars:
      by_team.setdefault(car.team, []).append(car)
    for pair in by_team.values():
      if len(pair) == 2:
        pair[0].teammate, pair[1].teammate = pair[1].idx, pair[0].idx

    quali = sorted(self.cars, key=lambda c: c.skill + self.rng.gauss(0, 0.25))
    for pos, car in enumerate(quali):
      car.grid = pos + 1
      car.dist = -pos * GRID_SPACING_M
      car.lap_noise = self.rng.gauss(0, car.noise_sigma)

    for car in self.cars:
      if car.idx == self.player_idx:
        car.compound = "MEDIUM"
        car.stints = ["MEDIUM"]
        continue
      self._plan_ai(car)

  def _plan_ai(self, car):
    laps = self.laps
    for _ in range(50):
      seq = self.rng.choice(ONE_STOP_PLANS if self.rng.random() < 0.65 else TWO_STOP_PLANS)
      weights = [TYRE_LIFE_LAPS[c] for c in seq]
      stints = [laps * w / sum(weights) + self.rng.uniform(-1.5, 1.5) for w in weights]
      stints = [max(3, int(round(s))) for s in stints[:-1]]
      last = laps - sum(stints)
      stints.append(last)
      if last >= 3 and all(s <= TYRE_LIFE_LAPS[c] * 0.95 for s, c in zip(stints, seq)):
        break
    else:
      seq, stints = ("MEDIUM", "HARD"), [laps // 2, laps - laps // 2]
    car.compound = seq[0]
    car.stints = [seq[0]]
    car.plan = []
    lap = 0
    for s, nxt in zip(stints[:-1], seq[1:]):
      lap += s
      car.plan.append((lap, nxt))

  def _setup_scenario(self):
    L = self.laps
    s = self.scenario
    if s in ("safety-car", "chaos"):
      self.retire_at = (0.40 if s == "safety-car" else 0.30) * L + 0.35
      self.caution_type = "FULL"
      self.caution_laps = 3
      self.retire_reason = "accident"
    elif s == "vsc":
      self.retire_at = 0.35 * L + 0.35
      self.caution_type = "VIRTUAL"
      self.caution_laps = 2
      self.retire_reason = "power unit"
    if s in ("rain", "chaos"):
      self.rain_start = (0.45 if s == "rain" else 0.55) * L
      self.rain_heavy_end = self.rain_start + 0.20 * L
    if s == "damage":
      self.damage_at = 0.30 * L + 0.35
    if s == "puncture":
      self.puncture_at = 0.55 * L + 0.2
    if s in ("high-deg", "chaos"):
      self.cars[self.player_idx].deg_mult = 1.8
    if s == "undercut":
      self.undercut_lap = max(3, round(0.45 * L) - 5)
    if s == "double-stack":
      self._setup_double_stack()

  def _setup_double_stack(self):
    player = self.cars[self.player_idx]
    mate = self.cars[player.teammate]
    box_lap = round(0.45 * self.laps)
    # Front row with a pace edge over the field, so no other car can split the pair before the stop
    others = sorted((c for c in self.cars if c not in (player, mate)), key=lambda c: c.grid)
    mate.grid, player.grid = 1, 2
    for pos, car in enumerate(others):
      car.grid = pos + 3
    for car in self.cars:
      car.dist = -(car.grid - 1) * GRID_SPACING_M
    mate.skill = min(c.skill for c in others) - 0.25
    player.skill = mate.skill + 0.09
    for car in (player, mate):
      car.noise_sigma = 0.05
      car.lap_noise = self.rng.gauss(0, car.noise_sigma)
      car.deg_mult = 1.0
    mate.compound = "MEDIUM"
    mate.stints = ["MEDIUM"]
    mate.plan = [(box_lap, "HARD")]

  # ---------- public API ----------

  def step(self, dt: float) -> None:
    if self.t == 0.0:
      self._emit("LIGHTS_OUT")
    remaining = float(dt)
    while remaining > 1e-9 and not self.finished:
      h = min(remaining, MAX_STEP_S)
      self._advance(h)
      remaining -= h

  def command_pit(self, compound: str) -> None:
    car = self.cars[self.player_idx]
    if car.status != "ACTIVE" or car.in_pit:
      return
    car.pit_pending = to_visual(compound)
    car.pit_unscheduled = True

  def cancel_pit(self) -> bool:
    car = self.cars[self.player_idx]
    if car.status != "ACTIVE" or car.in_pit or not car.pit_pending:
      return False
    car.pit_pending = None
    car.pit_unscheduled = False
    return True

  def player_lap(self) -> int:
    return self._current_lap(self.cars[self.player_idx])

  def player_pitted_since(self, lap: int) -> bool:
    car = self.cars[self.player_idx]
    return car.stops > 0 and car.last_stop_lap >= lap

  def results(self) -> list[dict]:
    out = []
    for pos, car in enumerate(self._classification()):
      out.append({
        "position": pos + 1,
        "idx": car.idx,
        "name": car.name,
        "total_time": round(car.finish_time, 3) if car.finish_time is not None else None,
        "laps": car.laps_done,
        "stops": car.stops,
        "compounds": list(car.stints),
        "status": car.status,
      })
    return out

  def frame(self) -> dict:
    order = self._classification()
    leader = order[0]
    rows = {}
    for pos, car in enumerate(order):
      if car.status == "RETIRED":
        gap_leader = gap_ahead = 0.0
      else:
        gap_leader = self._gap(car, leader) if pos else 0.0
        gap_ahead = self._gap(car, order[pos - 1]) if pos else 0.0
      rows[car.idx] = (pos + 1, gap_leader, gap_ahead)
    behind = {}
    for pos, car in enumerate(order):
      nxt = order[pos + 1] if pos + 1 < len(order) else None
      behind[car.idx] = rows[nxt.idx][2] if nxt is not None and nxt.status != "RETIRED" else 0.0

    caution = self.safety_car in ("FULL", "VIRTUAL")
    cars = [self._car_dict(car, rows[car.idx], behind[car.idx], caution) for car in self.cars]
    events, self.events = self.events, []
    return {
      "sessionUid": self.session_uid,
      "sessionTime": round(self.t, 3),
      "cars": cars,
      "track": self._track_dict(),
      "player_idx": self.player_idx,
      "lap": self.player_lap(),
      "events": events,
    }

  # ---------- simulation ----------

  def _leader_progress(self):
    return max(c.progress for c in self.cars if c.status != "RETIRED")

  def _rainfall(self, progress):
    if self.rain_start is None or progress < self.rain_start:
      return 0.0
    if progress < self.rain_start + 1.5:
      return 0.3
    if progress < self.rain_heavy_end:
      return 0.7
    if progress < self.rain_heavy_end + 1.5:
      return 0.3
    return 0.0

  def _emit(self, code, car=-1, detail=""):
    self.events.append({"code": code, "car": car, "detail": detail})
    who = self.cars[car].name if car >= 0 else ""
    self.log.append((self.t, f"{code} {who} {detail}".replace("  ", " ").strip()))

  def _advance(self, h):
    lead = self._leader_progress()
    self._scenario_triggers(lead)
    rain = self._rainfall(lead)
    if rain > 0:
      self.wetness = min(self.max_wetness, self.wetness + rain * WET_RAIN_RATE * h / BASE_LAP_S)
    else:
      self.wetness = max(0.0, self.wetness - WET_DRY_RATE * h / BASE_LAP_S)
    self._ai_reactions()
    self._advance_pits(h)
    self._assign_speeds()
    self._integrate(h)
    self._update_thermals(h)
    self.t += h
    if self.chequered and all(c.status != "ACTIVE" for c in self.cars):
      self.finished = True

  def _scenario_triggers(self, lead):
    player = self.cars[self.player_idx]
    if self.retire_at is not None and self.retired_idx is None and lead >= self.retire_at:
      order = [c for c in self._classification()
               if c.status == "ACTIVE" and c.idx != self.player_idx and not c.in_pit]
      pool = order[11:18] or order[-3:]
      victim = self.rng.choice(pool)
      victim.status = "RETIRED"
      victim.speed = 0.0
      victim.pit_pending = None
      self.retired_idx = victim.idx
      self._emit("RETIREMENT", victim.idx, self.retire_reason)
      self.caution_deploy_t = self.t + 8.0
    if self.caution_deploy_t is not None and self.t >= self.caution_deploy_t:
      self.caution_deploy_t = None
      self.safety_car = self.caution_type
      self.caution_end_lap = int(lead + self.caution_laps + 0.5)
      self.caution_returning_sent = False
      self._emit("SAFETY_CAR", -1, f"{self.caution_type} DEPLOYED")
      self._caution_pit_calls()
    if self.damage_at is not None and player.progress >= self.damage_at:
      self.damage_at = None
      player.wing = 45
      player.body = 8
      order = self._classification()
      pos = order.index(player)
      other = order[pos - 1] if pos > 0 else order[pos + 1]
      self._emit("COLLISION", player.idx, f"with {other.idx}")
    if self.puncture_at is not None and player.progress >= self.puncture_at:
      self.puncture_at = None
      player.tyre_damage = 100
      player.puncture_wheel = "FL"
      self.log.append((self.t, f"PUNCTURE {player.name} {player.puncture_wheel}"))
    if self.undercut_lap is not None and self.undercut_idx is None and player.laps_done >= self.undercut_lap - 1:
      order = self._classification()
      pos = order.index(player)
      for rival in reversed(order[:pos]):
        if rival.status == "ACTIVE" and not rival.in_pit and rival.pit_pending is None:
          rival.pit_pending = "HARD" if rival.compound != "HARD" else "MEDIUM"
          rival.pit_unscheduled = True
          rival.plan = []
          rival.next_out_lap_boost = UNDERCUT_OUT_LAP_BOOST_S
          self.undercut_idx = rival.idx
          self.log.append((self.t, f"UNDERCUT {rival.name} boxes this lap for {rival.pit_pending}"))
          break

  def _caution_pit_calls(self):
    for car in self.cars:
      if car.idx == self.player_idx or car.status != "ACTIVE" or car.in_pit or car.pit_pending:
        continue
      if not car.plan:
        continue
      planned_len = max(1, car.plan[0][0] - car.stint_start_lap)
      done = car.laps_done - car.stint_start_lap
      if done / planned_len >= 0.4:
        car.pit_pending = car.plan[0][1]
        car.pit_unscheduled = True

  def _ai_reactions(self):
    for car in self.cars:
      if car.idx == self.player_idx or car.status != "ACTIVE" or car.in_pit or car.pit_pending:
        continue
      if car.compound in DRY and self.wetness >= car.rain_threshold:
        car.pit_pending = "INTER"
        car.pit_unscheduled = True
      elif car.compound in ("INTER", "WET") and self.wetness < car.dry_threshold:
        left = self.laps - car.laps_done
        car.pit_pending = "SOFT" if left <= 12 else "MEDIUM"
        car.pit_unscheduled = True

  def _advance_pits(self, h):
    t_end = self.t + h
    while True:
      due = [c for c in self.cars if c.in_pit and c.pit_end_t <= t_end]
      if not due:
        return
      car = min(due, key=lambda c: (c.pit_end_t, c.idx))
      te = car.pit_end_t
      if car.pit_phase == "lane_in":
        mate = self.cars[car.teammate]
        if mate.idx != car.idx and mate.pit_phase == "box" and mate.pit_end_t > te:
          car.pit_phase = "queue"
          car.queue_from = te
          car.pit_end_t = mate.pit_end_t + DOUBLE_STACK_GAP_S
        else:
          self._enter_box(car, te)
      elif car.pit_phase == "queue":
        wait = te - car.queue_from
        car.queue_s += wait
        self.log.append((te, f"DOUBLE STACK {car.name} queued {wait:.1f}s behind {self.cars[car.teammate].name}"))
        self._enter_box(car, te)
      elif car.pit_phase == "box":
        car.pit_phase = "lane_out"
        car.pit_end_t = te + car.pit_lane_out_s
      else:
        car.pit_phase = None
        car.out_lap = True
        car.out_lap_boost = car.next_out_lap_boost
        car.next_out_lap_boost = 0.0

  def _enter_box(self, car, t):
    car.pit_phase = "box"
    car.pit_end_t = t + car.pit_box_s
    self._change_tyres(car)

  def _start_pit(self, car, t_cross):
    loss = PIT_LOSS_S * PIT_LOSS_FACTOR.get(self.safety_car, 1.0)
    wing = WING_CHANGE_S if car.wing > 0 else 0.0
    lane = max(0.0, loss - PIT_STATIONARY_S)
    car.pit_phase = "lane_in"
    car.pit_end_t = t_cross + lane / 2
    car.pit_box_s = PIT_STATIONARY_S + wing
    car.pit_lane_out_s = lane / 2
    car.pit_compound = car.pit_pending
    car.pit_pending = None
    car.last_stop_lap = car.laps_done
    car.dist = car.laps_done * TRACK_LENGTH
    car.speed = 0.0
    tag = f" under {self.safety_car}" if self.safety_car != "NONE" else ""
    self.log.append((t_cross, f"PIT {car.name} -> {car.pit_compound}{tag} (end of L{car.laps_done})"))
    if car.idx != self.player_idx and car.idx == self.cars[self.player_idx].teammate:
      self._emit("TEAMMATE_PIT", car.idx)

  def _change_tyres(self, car):
    new = car.pit_compound
    car.compound = new
    car.tyre_laps = 0.0
    car.stints.append(new)
    car.stops += 1
    car.wing = 0
    car.tyre_damage = 0
    car.stint_start_lap = car.laps_done
    for w in car.temps:
      car.temps[w] = 70.0
    if new in ("INTER", "WET"):
      car.used_wet = True
    if car.idx == self.player_idx:
      return
    scheduled = car.plan and car.plan[0][0] == car.laps_done and not car.pit_unscheduled
    if scheduled:
      car.plan.pop(0)
    else:
      car.plan = [p for p in car.plan if p[0] > car.laps_done + 3]
      if new in ("INTER", "WET"):
        car.plan = []
      else:
        self._replan_final(car)
    car.pit_unscheduled = False

  def _replan_final(self, car):
    left = self.laps - car.laps_done
    if car.plan:
      return
    life = TYRE_LIFE_LAPS[car.compound]
    if left <= life * 0.95:
      return
    used = {c for c in car.stints if c in DRY}
    nxt = "HARD" if car.compound != "HARD" else "MEDIUM"
    if len(used) >= 2:
      nxt = car.compound
    stint = int(left * life / (life + TYRE_LIFE_LAPS[nxt]))
    car.plan = [(car.laps_done + max(3, stint), nxt)]

  def _base_lap_time(self, car):
    comp = car.compound
    eff = car.tyre_laps * car.deg_mult
    t = BASE_LAP_S + car.skill + PACE_OFFSET_S.get(comp, 0.0) + DEG_S_PER_LAP[comp] * eff
    t += cliff_penalty(eff - TYRE_LIFE_LAPS[comp])
    t += FUEL_S_PER_KG * max(0.0, car.fuel)
    t += car.lap_noise
    t += weather_penalty(comp, self.wetness) + WET_TRACK_S_PER_UNIT * self.wetness
    if comp == "INTER" and self.wetness < 0.15:
      t += 0.25 * eff * (0.15 - self.wetness) / 0.15
    t += WING_DAMAGE_S_PER_PCT * car.wing
    if car.tyre_damage >= 100:
      t += PUNCTURE_S
    if car.laps_done == 0:
      t += START_PENALTY_S
    if car.out_lap:
      t += car.out_lap_boost
    if car.stops > 0 and car.tyre_laps < 2:
      t -= FRESH_TYRE_BONUS_S
    return t

  def _assign_speeds(self):
    running = sorted((c for c in self.cars if c.status == "ACTIVE"), key=lambda c: -c.dist)
    prev = None
    for car in running:
      if car.in_pit:
        car.speed = 0.0
        continue
      lap_t = self._base_lap_time(car)
      car.lap_t = lap_t
      v_green = TRACK_LENGTH / lap_t
      if self.safety_car == "FULL":
        if prev is None:
          v = TRACK_LENGTH / (BASE_LAP_S * SC_LAP_FACTOR)
        else:
          gap_d = prev.dist - car.dist
          target = SC_GAP_S * prev.speed
          v = prev.speed + (gap_d - target) / 2.0
          v = max(0.3 * prev.speed, min(v, v_green))
      elif self.safety_car == "VIRTUAL":
        v = TRACK_LENGTH / (lap_t * VSC_LAP_FACTOR)
      else:
        v = v_green
        if prev is not None and v > prev.speed:
          gap_d = prev.dist - car.dist
          if gap_d < FOLLOW_GAP_S * v and prev.lap_t - lap_t < OVERTAKE_DELTA_S:
            v = prev.speed
      car.speed = max(0.0, v)
      prev = car

  def _integrate(self, h):
    caution = self.safety_car in ("FULL", "VIRTUAL")
    for car in self.cars:
      if car.status != "ACTIVE" or car.in_pit or car.speed <= 0:
        continue
      d0 = car.dist
      d1 = d0 + car.speed * h
      if d1 > 0:
        k0 = math.floor(d0 / CHECKPOINT_M) if d0 >= 0 else 0
        k1 = math.floor(d1 / CHECKPOINT_M)
        for k in range(k0 + 1, k1 + 1):
          car.cp[k] = self.t + (k * CHECKPOINT_M - d0) / car.speed
        frac = (d1 - max(d0, 0.0)) / TRACK_LENGTH
        car.tyre_laps += frac
        car.fuel -= FUEL_BURN_KG * frac * (CAUTION_FUEL_FACTOR if caution else 1.0)
      lap0 = math.floor(d0 / TRACK_LENGTH) if d0 >= 0 else -1
      lap1 = math.floor(d1 / TRACK_LENGTH)
      car.dist = d1
      if lap1 > lap0 and lap1 >= 1:
        t_cross = self.t + (lap1 * TRACK_LENGTH - d0) / car.speed
        self._cross_line(car, t_cross)

  def _cross_line(self, car, t_cross):
    lap_time = t_cross - car.lap_start_t
    car.laps_done += 1
    car.last_lap = lap_time
    car.lap_times.append(lap_time)
    if car.best_lap == 0 or lap_time < car.best_lap:
      car.best_lap = lap_time
    car.out_lap = False
    car.out_lap_boost = 0.0

    if car.laps_done > self.leader_laps:
      self.leader_laps = car.laps_done
      if self.safety_car in ("FULL", "VIRTUAL"):
        if not self.caution_returning_sent and self.leader_laps >= self.caution_end_lap - 1:
          self.caution_returning_sent = True
          self._emit("SAFETY_CAR", -1, f"{self.safety_car} RETURNING")
        if self.leader_laps >= self.caution_end_lap:
          self._emit("SAFETY_CAR", -1, f"{self.safety_car} RETURNED")
          self.safety_car = "NONE"

    if self.chequered or car.laps_done >= self.laps:
      if not self.chequered:
        self.chequered = True
        self._emit("CHEQUERED_FLAG")
      car.status = "FINISHED"
      car.finish_time = t_cross
      car.dist = car.laps_done * TRACK_LENGTH
      car.speed = 0.0
      car.pit_pending = None
      return

    car.lap_start_t = t_cross
    car.lap_noise = self.rng.gauss(0, car.noise_sigma)
    if car.pit_pending:
      self._start_pit(car, t_cross)
      return
    if car.idx != self.player_idx and car.plan and car.plan[0][0] == car.laps_done + 1:
      car.pit_pending = car.plan[0][1]

  def _update_thermals(self, h):
    caution = self.safety_car in ("FULL", "VIRTUAL")
    k = min(1.0, 0.04 * h)
    for car in self.cars:
      if car.status != "ACTIVE":
        continue
      target = TYRE_TEMP_TARGET[car.compound]
      if car.compound == "INTER" and self.wetness < 0.15:
        target += (0.15 - self.wetness) * 200
      if caution:
        target -= 18
      if car.in_pit:
        target -= 10
      for w in car.temps:
        offset = 3.0 if w in ("FL", "FR") else 0.0
        car.temps[w] += (target + offset - car.temps[w]) * k + self.display_rng.uniform(-0.15, 0.15)
      if caution or car.in_pit:
        car.ers = min(100.0, car.ers + 1.5 * h)
      else:
        car.ers = min(100.0, max(15.0, car.ers + self.display_rng.uniform(-0.9, 0.85) * h))
      car.engine_temp += (108 + (0 if caution else 4) - car.engine_temp) * k

  # ---------- classification and timing ----------

  def _classification(self):
    return sorted(self.cars, key=lambda c: (
      c.status == "RETIRED",
      -c.dist,
      c.finish_time if c.finish_time is not None else float("inf"),
      c.grid,
    ))

  def _ref_time_at(self, ref, dist):
    k = math.floor(dist / CHECKPOINT_M)
    t0, t1 = ref.cp.get(k), ref.cp.get(k + 1)
    if t0 is None:
      return None
    if t1 is None:
      return t0
    return t0 + (dist - k * CHECKPOINT_M) / CHECKPOINT_M * (t1 - t0)

  def _gap(self, car, ref):
    if car.status == "ACTIVE" and car.speed <= 0 and car.dist >= CHECKPOINT_M:
      t_ref = self._ref_time_at(ref, car.dist)
      if t_ref is not None:
        return round(max(0.0, self.t - t_ref), 3)
    if car.dist >= CHECKPOINT_M:
      k = math.floor(car.dist / CHECKPOINT_M)
      while k > 0 and k not in car.cp:
        k -= 1
      tc, tr = car.cp.get(k), ref.cp.get(k)
      if tc is not None and tr is not None:
        return round(max(0.0, tc - tr), 3)
    return round(max(0.0, ref.dist - car.dist) / (TRACK_LENGTH / BASE_LAP_S), 3)

  def _current_lap(self, car):
    return min(self.laps, car.laps_done + 1) if car.status == "ACTIVE" else car.laps_done

  # ---------- frame building ----------

  def _track_dict(self):
    lead = self._leader_progress()
    rainy = self.rain_start is not None
    rain_now = self._rainfall(lead)
    forecast = []
    for minutes in (0, 5, 10, 15):
      rf = self._rainfall(lead + minutes * 60.0 / BASE_LAP_S)
      forecast.append({"minutes": minutes, "rain": int(round(rf * 100)), "weather": rain_label(rf, rainy)})
    track_temp = round(32 - 10 * min(1.0, self.wetness / 0.5))
    return {
      "id": TRACK_ID,
      "name": TRACK_NAME,
      "weather": rain_label(rain_now, rainy),
      "trackTemp": track_temp,
      "airTemp": 21 if rainy and self.wetness > 0.1 else 24,
      "totalLaps": self.laps,
      "trackLength": TRACK_LENGTH,
      "sessionType": "RACE",
      "safetyCar": self.safety_car,
      "rainForecast": forecast,
      "pitWindow": {"idealLap": 0, "latestLap": 0, "rejoinPosition": 0},
    }

  def _car_dict(self, car, row, gap_behind, caution):
    position, gap_leader, gap_ahead = row
    rng = self.display_rng
    lap_dist = car.dist % TRACK_LENGTH if car.dist >= 0 else car.dist
    frac = (lap_dist % TRACK_LENGTH) / TRACK_LENGTH
    i = int(frac * len(SPEED_PROFILE)) % len(SPEED_PROFILE)
    nxt = (i + 1) % len(SPEED_PROFILE)
    if car.status != "ACTIVE":
      speed = next_speed = 0.0
    elif car.pit_phase in ("box", "queue"):
      speed = next_speed = 0.0
    elif car.in_pit:
      speed = next_speed = 80.0
    else:
      avg = car.speed * 3.6
      speed = avg * SPEED_PROFILE[i] / PROFILE_MEAN
      next_speed = avg * SPEED_PROFILE[nxt] / PROFILE_MEAN
    gear = gear_from_speed(speed)
    throttle, brake = throttle_brake(speed, next_speed, rng) if speed > 0 else (0, 0)
    angle = frac * 2 * math.pi

    eff = car.tyre_laps * car.deg_mult
    base_wear = wear_percent(eff, TYRE_LIFE_LAPS[car.compound])
    wear = {w: round(min(100.0, base_wear * f * car.wear_jitter), 1) for w, f in WHEEL_WEAR.items()}
    if car.tyre_damage >= 100:
      wear[car.puncture_wheel] = 100.0

    if car.pit_phase in ("box", "queue"):
      pit_status = "IN_PIT_AREA"
    elif car.in_pit:
      pit_status = "PITTING"
    else:
      pit_status = "NONE"
    if car.pit_pending and not car.in_pit:
      driver_status = "IN_LAP"
    elif car.out_lap or car.in_pit:
      driver_status = "OUT_LAP"
    else:
      driver_status = "ON_TRACK"

    trend = "→"
    if len(car.lap_times) >= 2:
      if car.lap_times[-1] < car.lap_times[-2] - 0.2:
        trend = "↑"
      elif car.lap_times[-1] > car.lap_times[-2] + 0.2:
        trend = "↓"

    laps_left = max(0.0, self.laps - car.dist / TRACK_LENGTH)
    fuel_margin = (car.fuel - FUEL_BURN_KG * laps_left) / FUEL_BURN_KG
    current_lap_time = max(0.0, self.t - car.lap_start_t) if car.status == "ACTIVE" else 0.0
    drs = (not caution and car.status == "ACTIVE" and not car.in_pit and car.laps_done >= 2
           and 0 < gap_ahead < 1.0 and speed > 280)

    return {
      "idx": car.idx,
      "raceNumber": car.number,
      "name": car.name,
      "x": round(450 * math.cos(angle), 1),
      "z": round(450 * math.sin(angle), 1),
      "heading": round((math.degrees(angle) + 90) % 360, 1),
      "speed": round(speed, 1),
      "rpm": round(rpm_from_speed(speed, gear, rng)),
      "gear": gear,
      "throttle": throttle,
      "brake": brake,
      "fuel": round(max(0.0, car.fuel), 2),
      "engineTemp": round(car.engine_temp, 1),
      "tireTemps": {w: round(v, 1) for w, v in car.temps.items()},
      "tireWear": wear,
      "damage": int(car.wing),
      "compound": ALLOCATION[car.compound],
      "drs": drs,
      "ersBattery": round(car.ers, 1),
      "lapTime": round(current_lap_time, 3),
      "bestLapTime": round(car.best_lap, 3),
      "stintLaps": int(car.tyre_laps),
      "tyreAge": int(car.tyre_laps),
      "trend": trend,
      "cumulDist": round(car.dist, 1),
      "lapDist": round(lap_dist, 1),
      "position": position,
      "gapToLeader": gap_leader,
      "gapToAhead": gap_ahead,
      "gapToBehind": gap_behind,
      "gapToNext": gap_behind,
      "currentLap": self._current_lap(car),
      "lastLapTime": round(car.last_lap, 3),
      "pitStatus": pit_status,
      "numPitStops": car.stops,
      "driverStatus": driver_status,
      "resultStatus": car.status,
      "penaltiesSec": 0,
      "gridPosition": car.grid,
      "fiaFlag": "YELLOW" if caution else "GREEN",
      "visualCompound": car.compound,
      "fuelRemainingLaps": round(fuel_margin, 2),
      "tyreDamage": int(car.tyre_damage),
      "bodyDamage": int(car.body),
      "teamId": car.team_id,
      "team": car.team,
      "myTeam": False,
      "liveryColour": car.colour,
    }


# ---------- headless ----------

def scripted_policy(sim, box_lap=None):
  car = sim.cars[sim.player_idx]
  box_lap = box_lap or round(0.45 * sim.laps)
  if car.status != "ACTIVE" or car.in_pit or car.pit_pending:
    return
  left = sim.laps - car.laps_done
  if car.tyre_damage >= 100 or car.wing > 0:
    sim.command_pit("HARD" if left > 14 else "MEDIUM")
  elif car.compound in DRY and sim.wetness >= 0.25:
    sim.command_pit("INTER")
  elif car.compound in ("INTER", "WET") and sim.wetness < 0.15:
    sim.command_pit("SOFT" if left <= 12 else "MEDIUM")
  elif car.stops == 0 and sim.safety_car in ("FULL", "VIRTUAL") and car.laps_done >= 0.3 * sim.laps:
    sim.command_pit("HARD")
  elif car.stops == 0 and car.laps_done >= box_lap - 1:
    sim.command_pit("HARD")


def run_headless(scenario, laps, seed, policy=True, box_lap=None):
  sim = RaceSim(scenario=scenario, laps=laps, seed=seed)
  player = sim.cars[sim.player_idx]
  print(f"Scenario {scenario}: {SCENARIOS[scenario]} ({laps} laps, seed {seed})")
  print(f"{'Lap':>3} {'Pos':>3} {'Tyre':>12} {'Wear':>5} {'Last':>9} {'Ahead':>10} {'Behind':>6} {'SC':>7} {'Wet':>4} {'Stops':>5}")
  print("-" * 82)
  seen_lap = 0
  log_idx = 0
  while not sim.finished:
    sim.step(1.0)
    if policy:
      scripted_policy(sim, box_lap)
    while log_idx < len(sim.log):
      t, text = sim.log[log_idx]
      log_idx += 1
      print(f"      [{t:7.1f}s L{sim.leader_laps + 1:>2}] {text}")
    if player.laps_done > seen_lap or (player.status != "ACTIVE" and seen_lap < player.laps_done):
      seen_lap = player.laps_done
      pending = sim.events
      f = sim.frame()
      sim.events = pending
      pc = f["cars"][sim.player_idx]
      ahead = next((c["name"] for c in f["cars"] if c["position"] == pc["position"] - 1), "---")
      tyre = f"{pc['visualCompound']}({pc['compound']}) {pc['tyreAge']:>2}"
      print(f"{seen_lap:>3} P{pc['position']:>2} {tyre:>12} {max(pc['tireWear'].values()):>5.0f} "
            f"{fmt_lap(pc['lastLapTime']):>9} {ahead:>3} {pc['gapToAhead']:>6.1f} {pc['gapToBehind']:>6.1f} "
            f"{sim.safety_car:>7} {sim.wetness:>4.2f} {pc['numPitStops']:>5}")
    if sim.t > laps * BASE_LAP_S * 3:
      print("Simulation did not finish in time")
      break
  print("\nResults")
  for r in sim.results()[:10]:
    print(f"  P{r['position']:>2} {r['name']} {r['status']:<8} stops {r['stops']} {'-'.join(r['compounds'])}")
  for r in sim.results():
    if r["idx"] == sim.player_idx:
      print(f"  Player: P{r['position']} {r['status']} stops {r['stops']} {'-'.join(r['compounds'])} "
            f"queued {player.queue_s:.1f}s in double stacks")
  return sim


def run_dry():
  sim = RaceSim()
  print(f"{'t':>6} {'Lap':>3} {'Pos':>3} {'Speed':>5} {'Gear':>2} {'Comp':>5} {'ERS':>4} {'DRS':>3} {'Fuel':>5} {'WearFL':>6} {'TFL':>4} {'Laptime':>9}", flush=True)
  print("-" * 80, flush=True)
  for _ in range(100):
    sim.step(2.0)
    pc = sim.frame()["cars"][sim.player_idx]
    print(
      f"{sim.t:>6.0f} {pc['currentLap']:>3} P{pc['position']:>2} {pc['speed']:>5.0f} {pc['gear']:>2} "
      f"{pc['compound']:>5} {pc['ersBattery']:>4.0f} {'Y' if pc['drs'] else 'N':>3} "
      f"{pc['fuel']:>5.1f} {pc['tireWear']['FL']:>6.1f} {pc['tireTemps']['FL']:>4.0f} "
      f"{fmt_lap(pc['lastLapTime']):>9}",
      flush=True,
    )


# ---------- live runner ----------

async def listen_radio(url, sim):
  pending_call = None
  while not sim.finished:
    try:
      async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=None) as ws:
        print(f"Listening for engineer calls on {url}", flush=True)
        async for raw in ws:
          try:
            msg = json.loads(raw)
          except (TypeError, ValueError):
            continue
          if not isinstance(msg, dict):
            continue
          if not msg.get("box_this_lap"):
            if (msg.get("type") == "strategy" and pending_call is not None
                and msg.get("lap") == pending_call and not sim.player_pitted_since(pending_call)):
              if sim.cancel_pit():
                print("Engineer: stay out (box call withdrawn)", flush=True)
              pending_call = None
            continue
          call_lap = int(msg.get("lap") or sim.player_lap())
          if sim.player_pitted_since(call_lap):
            continue
          compound = (msg.get("plan") or {}).get("next_compound") or "HARD"
          sim.command_pit(compound)
          pending_call = call_lap
          print(f"Engineer: box this lap (L{call_lap}) for {to_visual(compound)}", flush=True)
    except asyncio.CancelledError:
      raise
    except Exception as e:
      print(f"Pit radio connection lost or failed: {e}", flush=True)
      await asyncio.sleep(RECONNECT_DELAY)


async def run_live(args):
  sim = RaceSim(scenario=args.scenario, laps=args.laps, seed=args.seed)
  base = args.server.rstrip("/")
  telemetry_url = f"{base}/ws/telemetry"
  listener = asyncio.create_task(listen_radio(f"{base}/ws/pitradio", sim))
  interval = 1.0 / max(0.5, args.rate)
  seen_lap = sim.cars[sim.player_idx].laps_done
  print(f"Scenario {args.scenario}: {SCENARIOS[args.scenario]} ({args.laps} laps, x{args.speed} speed)", flush=True)
  try:
    while not sim.finished:
      try:
        async with websockets.connect(telemetry_url, ping_interval=20, ping_timeout=20, close_timeout=10) as ws:
          print("Simulator connected to Server", flush=True)
          last = time.monotonic()
          while not sim.finished:
            now = time.monotonic()
            sim.step((now - last) * args.speed)
            last = now
            pending_events = list(sim.events)
            frame = sim.frame()
            await ws.send(json.dumps(frame, ensure_ascii=False))
            for ev in pending_events:
              who = sim.cars[ev["car"]].name if ev["car"] >= 0 else ""
              print(f"  event {ev['code']} {who} {ev['detail']}".rstrip(), flush=True)
            player = sim.cars[sim.player_idx]
            if player.laps_done > seen_lap:
              seen_lap = player.laps_done
              pc = frame["cars"][sim.player_idx]
              print(
                f"L{seen_lap:>2} P{pc['position']:>2} {pc['visualCompound']}({pc['compound']}) age {pc['tyreAge']:>2} "
                f"last {fmt_lap(pc['lastLapTime'])} ahead +{pc['gapToAhead']:.1f} behind {pc['gapToBehind']:.1f} "
                f"SC {sim.safety_car} wet {sim.wetness:.2f} stops {pc['numPitStops']}",
                flush=True,
              )
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - now)))
      except (
        websockets.exceptions.ConnectionClosedError,
        websockets.exceptions.ConnectionClosedOK,
        ConnectionRefusedError,
        OSError,
      ) as e:
        print(f"Connection lost or failed: {e}", flush=True)
        print(f"Reconnecting in {RECONNECT_DELAY}s...", flush=True)
        await asyncio.sleep(RECONNECT_DELAY)
  finally:
    listener.cancel()
  print("Race finished", flush=True)
  for r in sim.results():
    if r["position"] <= 10 or r["idx"] == sim.player_idx:
      print(f"  P{r['position']:>2} {r['name']} {r['status']:<8} stops {r['stops']} {'-'.join(r['compounds'])}", flush=True)


def main():
  parser = argparse.ArgumentParser(description="Box-Box race simulator")
  parser.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
  parser.add_argument("--laps", type=int, default=30)
  parser.add_argument("--seed", type=int, default=1)
  parser.add_argument("--speed", type=float, default=1.0, help="sim seconds per real second")
  parser.add_argument("--rate", type=float, default=4.0, help="frames per real second")
  parser.add_argument("--server", default="ws://127.0.0.1:8000")
  parser.add_argument("--headless", action="store_true", help="run the race at full speed with a scripted player")
  parser.add_argument("--dry", action="store_true", help="print 100 player frames without a server")
  parser.add_argument("--box-lap", type=int, help="headless: lap the scripted player makes its first stop on")
  args = parser.parse_args()
  if args.dry:
    run_dry()
  elif args.headless:
    run_headless(args.scenario, args.laps, args.seed, box_lap=args.box_lap)
  else:
    print("Starting race telemetry simulator...", flush=True)
    try:
      asyncio.run(run_live(args))
    except KeyboardInterrupt:
      print("\nSimulator stopped by user", flush=True)


if __name__ == "__main__":
  main()
