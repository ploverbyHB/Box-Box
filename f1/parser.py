"""
F1 UDP parser — every car in the session (official PacketHeader / payload layout).
Car counts and layouts that differ between games come from the formats registered in f1/f1_XX.
"""
import math
import struct

from .formats import FORMATS

# 2024-2026 header: format, year, maj, min, packetVer, packetId, sessionUID, ...
HEADER_FMT = struct.Struct("<HBBBBBQfIIBB")
HEADER_SIZE = HEADER_FMT.size  # 29

# CarMotionData: world position X/Z and forward direction X/Z (int16, normalised)
MOTION_POSITION_FMT = struct.Struct("<3f")
MOTION_FORWARD_OFFSET = 24
MOTION_FORWARD_FMT = struct.Struct("<3h")

CAR_STATUS_FUEL_OFFSET = 5
CAR_STATUS_FUEL_LAPS_OFFSET = 13  # m_fuelRemainingLaps (MFD value)
CAR_STATUS_COMPOUND_OFFSET = 25  # m_actualTyreCompound
CAR_STATUS_VISUAL_COMPOUND_OFFSET = 26  # m_visualTyreCompound
CAR_STATUS_FIA_FLAGS_OFFSET = 28  # m_vehicleFiaFlags (int8)
CAR_STATUS_ERS_OFFSET = 37  # m_ersStoreEnergy (Joules)
ERS_MAX_J = 4_000_000.0

CAR_DMG_WEAR_OFFSET = 0  # 4 × float percentage
CAR_DMG_TYRE_DAMAGE_OFFSET = 16  # uint8[4] after the wear floats
# rear wing, floor, diffuser, sidepod follow the two front wing bytes
CAR_DMG_BODY_AFTER_WING = 2
CAR_DMG_BODY_COUNT = 4

# Session payload offsets (identical in 2024-2026; 2026 only appends after them)
SESSION_TOTAL_LAPS_OFFSET = 3
SESSION_TRACK_LENGTH_OFFSET = 4  # uint16
SESSION_TYPE_OFFSET = 6
SESSION_TRACK_ID_OFFSET = 7  # int8
SESSION_SAFETY_CAR_OFFSET = 124  # after 21 × 5-byte marshal zones
SESSION_NUM_FORECAST_OFFSET = 126
SESSION_FORECAST_OFFSET = 127
SESSION_PIT_WINDOW_OFFSET = 653  # ideal lap, latest lap, rejoin position
FORECAST_SAMPLE_FMT = struct.Struct("<BBBbbbbB")  # 8 bytes
FORECAST_MAX_SAMPLES = 64

# LapData offsets past the deltas
LAP_POSITION_OFFSET = 32
LAP_PIT_STATUS_OFFSET = 34
LAP_NUM_PIT_STOPS_OFFSET = 35
LAP_PENALTIES_OFFSET = 38
LAP_GRID_POSITION_OFFSET = 43
LAP_DRIVER_STATUS_OFFSET = 44
LAP_RESULT_STATUS_OFFSET = 45

# Event packet: 4-char code, then the details union
EVENT_CODE_OFFSET = HEADER_SIZE
EVENT_DETAIL_OFFSET = HEADER_SIZE + 4

WEATHER_NAMES = {
  0: "Clear", 1: "LightCloud", 2: "Overcast",
  3: "LightRain", 4: "HeavyRain", 5: "Storm",
}
SESSION_TYPES = {
  0: "UNKNOWN",
  1: "PRACTICE", 2: "PRACTICE", 3: "PRACTICE", 4: "PRACTICE",
  5: "QUALIFYING", 6: "QUALIFYING", 7: "QUALIFYING", 8: "QUALIFYING", 9: "QUALIFYING",
  10: "SPRINT_SHOOTOUT", 11: "SPRINT_SHOOTOUT", 12: "SPRINT_SHOOTOUT",
  13: "SPRINT_SHOOTOUT", 14: "SPRINT_SHOOTOUT",
  15: "RACE", 16: "RACE", 17: "RACE",
  18: "TIME_TRIAL",
}
SAFETY_CAR_TYPES = {0: "NONE", 1: "FULL", 2: "VIRTUAL", 3: "FORMATION"}
SAFETY_CAR_EVENTS = {0: "DEPLOYED", 1: "RETURNING", 2: "RETURNED", 3: "RESUME_RACE"}
PIT_STATUSES = {0: "NONE", 1: "PITTING", 2: "IN_PIT_AREA"}
DRIVER_STATUSES = {0: "GARAGE", 1: "FLYING_LAP", 2: "IN_LAP", 3: "OUT_LAP", 4: "ON_TRACK"}
RESULT_STATUSES = {
  0: "INVALID", 1: "INACTIVE", 2: "ACTIVE", 3: "FINISHED",
  4: "DNF", 5: "DSQ", 6: "NOT_CLASSIFIED", 7: "RETIRED",
}
FIA_FLAGS = {-1: "UNKNOWN", 0: "NONE", 1: "GREEN", 2: "BLUE", 3: "YELLOW"}
VISUAL_COMPOUNDS = {16: "SOFT", 17: "MEDIUM", 18: "HARD", 7: "INTER", 8: "WET"}
RETIREMENT_REASONS = {
  0: "invalid", 1: "retired", 2: "finished", 3: "terminal damage", 4: "inactive",
  5: "not enough laps completed", 6: "black flagged", 7: "red flagged",
  8: "mechanical failure", 9: "session skipped", 10: "session simulated",
}
COLLISION_SEVERITIES = {0: "low", 1: "medium", 2: "high"}
PENALTY_TYPES = {
  0: "drive through", 1: "stop go", 2: "grid penalty", 3: "penalty reminder",
  4: "time penalty", 5: "warning", 6: "disqualified", 7: "removed from formation lap",
  8: "parked too long timer", 9: "tyre regulations", 10: "this lap invalidated",
  11: "this and next lap invalidated", 12: "this lap invalidated without reason",
  13: "this and next lap invalidated without reason", 14: "this and previous lap invalidated",
  15: "this and previous lap invalidated without reason", 16: "retired", 17: "black flag timer",
}

INFRINGEMENT_TYPES = {
  0: "blocking by slow driving", 1: "blocking by wrong way driving", 2: "reversing off the start line",
  3: "big collision", 4: "small collision", 5: "collision failed to hand back position",
  6: "collision failed to hand back positions", 7: "corner cutting gained time",
  8: "corner cutting overtake", 9: "corner cutting overtakes", 10: "crossed pit exit lane",
  11: "ignoring blue flags", 12: "ignoring yellow flags", 13: "ignoring drive through",
  14: "too many drive throughs", 15: "drive through reminder, serve within n laps",
  16: "drive through reminder, serve this lap", 17: "pit lane speeding", 18: "parked for too long",
  19: "ignoring tyre regulations", 20: "too many penalties", 21: "multiple warnings",
  22: "approaching disqualification", 23: "tyre regulations select single",
  24: "tyre regulations select multiple", 25: "lap invalidated corner cutting",
  26: "lap invalidated running wide", 27: "ran wide gained time minor", 28: "ran wide gained time significant",
  29: "ran wide gained time extreme", 30: "lap invalidated wall riding", 31: "lap invalidated flashback used",
  32: "lap invalidated reset to track", 33: "blocking the pitlane", 34: "jump start",
  35: "safety car to car collision", 36: "safety car illegal overtake", 37: "safety car exceeding allowed pace",
  38: "virtual safety car exceeding allowed pace", 39: "formation lap below allowed speed",
  40: "formation lap parking", 41: "retired mechanical failure", 42: "retired terminally damaged",
  43: "safety car falling too far back", 44: "black flag timer", 45: "unserved stop go penalty",
  46: "unserved drive through penalty", 47: "engine component change", 48: "gearbox change",
  49: "parc ferme change", 50: "league grid penalty", 51: "retry penalty", 52: "illegal time gain",
  53: "mandatory pitstop", 54: "attribute assigned",
}

# LapData is 57 bytes in 2024-2026 (lastLap/currentLap are uint32 ms)
LAP_DATA_CAR_SIZE = 57

# Wheel arrays in every packet are ordered RL, RR, FL, FR
_wheel_map = ["RL", "RR", "FL", "FR"]

# Layout used for packet formats no f1_XX folder registers
DEFAULT_FORMAT = "2024"

# Packet IDs that update on-track car state enough to refresh the UI
_UI_PACKET_IDS = {0, 2, 4, 6, 7, 10}

_EMPTY_PIT_WINDOW = {"idealLap": 0, "latestLap": 0, "rejoinPosition": 0}


class F1Accumulator:
  def __init__(self, udp_format: str = "auto"):
    udp_format = str(udp_format).strip().lower()
    if udp_format != "auto" and FORMATS.find(udp_format) is None:
      raise ValueError(
        f"F1 UDP format must be auto or one of {', '.join(FORMATS.names())}, got {udp_format!r}"
      )
    self.udp_format = udp_format
    self._unsupported_seen = set()
    self._reset()

  def _reset(self):
    self.cars = {}
    self.player_idx = 0
    self.track_id = -1
    self.lap = 0
    self.session_type = 0
    self.weather = 0
    self.track_temp = 0
    self.air_temp = 0
    self.total_laps = 0
    self.track_length = 0
    self.safety_car = 0
    self.rain_forecast = []
    self.pit_window = dict(_EMPTY_PIT_WINDOW)
    self.session_uid = "0"
    self.pending_events = []
    self.format = FORMATS.get(DEFAULT_FORMAT)

  def feed(self, data: bytes, player_idx: int | None = None):
    if len(data) < HEADER_SIZE:
      return None

    header = HEADER_FMT.unpack_from(data, 0)
    packet_format = header[0]
    packet_id = header[5]
    session_uid = str(header[6])
    if session_uid != self.session_uid and header[6]:
      self._reset()
    self.session_uid = session_uid
    self.session_time = header[7]
    if player_idx is None:
      player_idx = header[10]
    self.player_idx = player_idx

    self.format = self._resolve_format(packet_format)

    if packet_id == 0:
      self._parse_motion(data)
    elif packet_id == 1:
      self._parse_session(data)
    elif packet_id == 2:
      self._parse_lap_data(data)
    elif packet_id == 3:
      self._parse_event(data)
    elif packet_id == 4:
      self._parse_participants(data)
    elif packet_id == 6:
      self._parse_car_telemetry(data)
    elif packet_id == 7:
      self._parse_car_status(data)
    elif packet_id == 10:
      self._parse_car_damage(data)
    else:
      # Lobby / history etc. — no UI frame
      return None

    if packet_id not in _UI_PACKET_IDS:
      return None
    if not self.cars:
      return None

    return self._build_frame()

  def _resolve_format(self, packet_format: int):
    if self.udp_format != "auto":
      return FORMATS.get(self.udp_format)
    fmt = FORMATS.find(str(packet_format))
    if fmt is None:
      if packet_format not in self._unsupported_seen:
        self._unsupported_seen.add(packet_format)
        print(f"F1 UDP format {packet_format} is not registered "
              f"({', '.join(FORMATS.names())}); parsing it as {DEFAULT_FORMAT}")
      fmt = FORMATS.get(DEFAULT_FORMAT)
    return fmt

  def _parse_motion(self, data):
    size = self.format.motion.size
    for i in range(self.format.num_cars):
      offset = HEADER_SIZE + i * size
      if offset + size > len(data):
        break
      x, _, z = MOTION_POSITION_FMT.unpack_from(data, offset)
      fwd_x, _, fwd_z = MOTION_FORWARD_FMT.unpack_from(data, offset + MOTION_FORWARD_OFFSET)
      car = self.cars.setdefault(i, {})
      car["x"] = round(x, 1)
      car["z"] = round(z, 1)
      fx, fz = fwd_x / 32767.0, fwd_z / 32767.0
      car["heading"] = round((math.degrees(math.atan2(fx, fz)) + 360) % 360, 1)

  def _parse_session(self, data):
    if len(data) < HEADER_SIZE + 8:
      return
    self.weather = data[HEADER_SIZE]
    self.track_temp, self.air_temp = struct.unpack_from("<bb", data, HEADER_SIZE + 1)
    # weather, trackTemp, airTemp, totalLaps, trackLength(u16), sessionType, trackId
    self.total_laps = data[HEADER_SIZE + SESSION_TOTAL_LAPS_OFFSET]
    self.track_length = struct.unpack_from("<H", data, HEADER_SIZE + SESSION_TRACK_LENGTH_OFFSET)[0]
    self.session_type = data[HEADER_SIZE + SESSION_TYPE_OFFSET]
    self.track_id = struct.unpack_from("<b", data, HEADER_SIZE + SESSION_TRACK_ID_OFFSET)[0]
    if len(data) > HEADER_SIZE + SESSION_NUM_FORECAST_OFFSET:
      self.safety_car = data[HEADER_SIZE + SESSION_SAFETY_CAR_OFFSET]
      count = min(data[HEADER_SIZE + SESSION_NUM_FORECAST_OFFSET], FORECAST_MAX_SAMPLES)
      forecast = []
      for i in range(count):
        offset = HEADER_SIZE + SESSION_FORECAST_OFFSET + i * FORECAST_SAMPLE_FMT.size
        if offset + FORECAST_SAMPLE_FMT.size > len(data):
          break
        sample = FORECAST_SAMPLE_FMT.unpack_from(data, offset)
        # sessionType, timeOffset, weather, trackTemp, trackTempChange, airTemp, airTempChange, rain%
        if sample[0] != self.session_type:
          continue
        forecast.append({
          "minutes": sample[1],
          "rain": sample[7],
          "weather": WEATHER_NAMES.get(sample[2], "Clear"),
        })
      self.rain_forecast = sorted(forecast, key=lambda f: f["minutes"])
    if len(data) >= HEADER_SIZE + SESSION_PIT_WINDOW_OFFSET + 3:
      ideal, latest, rejoin = data[HEADER_SIZE + SESSION_PIT_WINDOW_OFFSET:HEADER_SIZE + SESSION_PIT_WINDOW_OFFSET + 3]
      self.pit_window = {"idealLap": ideal, "latestLap": latest, "rejoinPosition": rejoin}

  def _parse_lap_data(self, data):
    for i in range(self.format.num_cars):
      offset = HEADER_SIZE + i * LAP_DATA_CAR_SIZE
      if offset + LAP_DATA_CAR_SIZE > len(data):
        break
      car = self.cars.setdefault(i, {})
      last_ms, cur_ms = struct.unpack_from("<II", data, offset)
      # deltas: uint16 ms part + uint8 minutes part
      ahead_ms, ahead_min = struct.unpack_from("<HB", data, offset + 14)
      lead_ms, lead_min = struct.unpack_from("<HB", data, offset + 17)
      lap_dist = struct.unpack_from("<f", data, offset + 20)[0]
      total_dist = struct.unpack_from("<f", data, offset + 24)[0]
      position = data[offset + LAP_POSITION_OFFSET]
      current_lap = data[offset + 33]
      car["lapTime"] = round(cur_ms / 1000.0, 3) if cur_ms else 0
      car["currentLap"] = current_lap
      car["lastLapTime"] = round(last_ms / 1000.0, 3) if last_ms else 0.0
      car["pitStatus"] = PIT_STATUSES.get(data[offset + LAP_PIT_STATUS_OFFSET], "NONE")
      car["numPitStops"] = data[offset + LAP_NUM_PIT_STOPS_OFFSET]
      car["penaltiesSec"] = data[offset + LAP_PENALTIES_OFFSET]
      car["gridPosition"] = data[offset + LAP_GRID_POSITION_OFFSET]
      car["driverStatus"] = DRIVER_STATUSES.get(data[offset + LAP_DRIVER_STATUS_OFFSET], "GARAGE")
      car["resultStatus"] = RESULT_STATUSES.get(data[offset + LAP_RESULT_STATUS_OFFSET], "INVALID")
      car["lapDist"] = round(lap_dist, 1)
      if last_ms:
        last_s = round(last_ms / 1000.0, 3)
        prev_best = car.get("bestLapTime") or 0
        if prev_best <= 0 or last_s < prev_best:
          car["bestLapTime"] = last_s
      car["position"] = position
      car["cumulDist"] = round(total_dist, 1)
      car["gapToAhead"] = round((ahead_min * 60000 + ahead_ms) / 1000.0, 3)
      car["gapToLeader"] = round((lead_min * 60000 + lead_ms) / 1000.0, 3)
      if i == self.player_idx:
        self.lap = current_lap

    # gap to car behind = ahead gap of the car one place behind
    by_pos = {}
    for idx, c in self.cars.items():
      pos = c.get("position") or 0
      if pos:
        by_pos[pos] = idx
    for idx, c in self.cars.items():
      pos = c.get("position") or 0
      behind_idx = by_pos.get(pos + 1)
      if behind_idx is not None:
        c["gapToBehind"] = self.cars[behind_idx].get("gapToAhead", 0)
      else:
        c["gapToBehind"] = 0
      # keep old key for UI compatibility
      c["gapToNext"] = c.get("gapToBehind", 0)

  def _parse_participants(self, data):
    if len(data) < HEADER_SIZE + 1:
      return
    layout = self.format.participants
    for i in range(self.format.num_cars):
      offset = HEADER_SIZE + 1 + i * layout.size
      if offset + layout.size > len(data):
        break
      team_id = struct.unpack_from(layout.team_id, data, offset + layout.team_id_offset)[0]
      race_number = data[offset + layout.race_number_offset]
      name_start = offset + layout.name_offset
      raw_name = data[name_start:name_start + layout.name_len].split(b"\x00", 1)[0]
      name = raw_name.decode("utf-8", errors="ignore").strip()
      car = self.cars.setdefault(i, {})
      car["raceNumber"] = race_number
      car["teamId"] = team_id
      car["team"] = self.format.teams.get(team_id, "")
      car["myTeam"] = data[offset + layout.my_team_offset] == 1
      car["liveryColour"] = ""
      livery = layout.livery_offset
      if livery is not None and data[offset + livery] >= 1:
        r, g, b = data[offset + livery + 1:offset + livery + 4]
        car["liveryColour"] = f"#{r:02X}{g:02X}{b:02X}"
      if name:
        car["name"] = name

  def _parse_car_telemetry(self, data):
    tele = self.format.telemetry.car
    for i in range(self.format.num_cars):
      offset = HEADER_SIZE + i * tele.size
      if offset + tele.size > len(data):
        break
      raw = tele.unpack_from(data, offset)
      car = self.cars.setdefault(i, {})
      surface_temps = raw[14:18]
      inner_temps = raw[18:22]
      car.update({
        "speed": raw[0],
        "throttle": round(raw[1] * 100, 1),
        "brake": round(raw[3] * 100, 1),
        "gear": raw[5],
        "rpm": raw[6],
        "drs": bool(raw[7]),
        "engineTemp": raw[22],
        "tireTemps": {
          _wheel_map[j]: max(surface_temps[j], inner_temps[j]) for j in range(4)
        },
      })

  def _parse_car_status(self, data):
    size = self.format.status.size
    for i in range(self.format.num_cars):
      offset = HEADER_SIZE + i * size
      if offset + size > len(data):
        continue
      car = self.cars.setdefault(i, {})
      fuel = struct.unpack_from("<f", data, offset + CAR_STATUS_FUEL_OFFSET)[0]
      car["fuel"] = round(fuel, 2)
      compound_id = data[offset + CAR_STATUS_COMPOUND_OFFSET]
      car["compound"] = self.format.compounds.get(compound_id, f"T{compound_id}")
      car["stintLaps"] = data[offset + 27]  # m_tyresAgeLaps
      car["visualCompound"] = VISUAL_COMPOUNDS.get(data[offset + CAR_STATUS_VISUAL_COMPOUND_OFFSET], "")
      flag = struct.unpack_from("<b", data, offset + CAR_STATUS_FIA_FLAGS_OFFSET)[0]
      car["fiaFlag"] = FIA_FLAGS.get(flag, "UNKNOWN")
      fuel_laps = struct.unpack_from("<f", data, offset + CAR_STATUS_FUEL_LAPS_OFFSET)[0]
      car["fuelRemainingLaps"] = round(fuel_laps, 2)
      ers_j = struct.unpack_from("<f", data, offset + CAR_STATUS_ERS_OFFSET)[0]
      car["ersBattery"] = round(max(0.0, min(100.0, (ers_j / ERS_MAX_J) * 100.0)), 1)

  def _parse_car_damage(self, data):
    size = self.format.damage.size
    wing = self.format.damage.front_wing
    for i in range(self.format.num_cars):
      offset = HEADER_SIZE + i * size
      if offset + size > len(data):
        # accept slightly shorter if trailing fields missing
        if offset + wing + 2 > len(data):
          continue
      car = self.cars.setdefault(i, {})
      if offset + 16 <= len(data):
        wears = struct.unpack_from("<4f", data, offset + CAR_DMG_WEAR_OFFSET)
        car["tireWear"] = {
          _wheel_map[j]: round(wears[j], 1) for j in range(4)
        }
      if offset + CAR_DMG_TYRE_DAMAGE_OFFSET + 4 <= len(data):
        car["tyreDamage"] = max(data[offset + CAR_DMG_TYRE_DAMAGE_OFFSET:offset + CAR_DMG_TYRE_DAMAGE_OFFSET + 4])
      if offset + wing + 2 <= len(data):
        fl = data[offset + wing]
        fr = data[offset + wing + 1]
        car["damage"] = max(fl, fr)
      body = offset + wing + CAR_DMG_BODY_AFTER_WING
      if body + CAR_DMG_BODY_COUNT <= len(data):
        car["bodyDamage"] = max(data[body:body + CAR_DMG_BODY_COUNT])

  def _parse_event(self, data):
    if len(data) < EVENT_DETAIL_OFFSET + 1:
      return
    code = data[EVENT_CODE_OFFSET:EVENT_CODE_OFFSET + 4].decode("ascii", errors="ignore")
    detail = data[EVENT_DETAIL_OFFSET:]
    event = None
    if code == "SCAR" and len(detail) >= 2:
      sc_type = SAFETY_CAR_TYPES.get(detail[0], "NONE")
      sc_event = SAFETY_CAR_EVENTS.get(detail[1], "DEPLOYED")
      event = {"code": "SAFETY_CAR", "car": -1, "detail": f"{sc_type} {sc_event}"}
    elif code == "RDFL":
      event = {"code": "RED_FLAG", "car": -1, "detail": ""}
    elif code == "RTMT":
      reason = ""
      if self.format.events.retirement_reason and len(detail) >= 2:
        reason = RETIREMENT_REASONS.get(detail[1], "")
      event = {"code": "RETIREMENT", "car": detail[0], "detail": reason}
    elif code == "PENA" and len(detail) >= 7:
      # penaltyType, infringementType, vehicleIdx, otherVehicleIdx, time, lapNum, placesGained
      penalty = PENALTY_TYPES.get(detail[0], f"penalty {detail[0]}")
      reason = INFRINGEMENT_TYPES.get(detail[1], f"infringement {detail[1]}")
      event = {"code": "PENALTY", "car": detail[2], "detail": f"{penalty} ({reason}) {detail[4]}s lap {detail[5]}"}
    elif code == "TMPT":
      event = {"code": "TEAMMATE_PIT", "car": detail[0], "detail": ""}
    elif code == "CHQF":
      event = {"code": "CHEQUERED_FLAG", "car": -1, "detail": ""}
    elif code == "LGOT":
      event = {"code": "LIGHTS_OUT", "car": -1, "detail": ""}
    elif code == "COLL" and len(detail) >= 2:
      text = f"with {detail[1]}"
      if self.format.events.collision_severity and len(detail) >= 3:
        text += f" ({COLLISION_SEVERITIES.get(detail[2], 'unknown')} severity)"
      event = {"code": "COLLISION", "car": detail[0], "detail": text}
    if event:
      self.pending_events.append(event)

  def _build_frame(self):
    all_cars = []
    for idx in sorted(self.cars.keys()):
      c = self.cars[idx]
      all_cars.append({
        "idx": idx,
        "raceNumber": c.get("raceNumber", idx),
        "name": c.get("name", f"#{idx}"),
        "x": c.get("x", 0),
        "z": c.get("z", 0),
        "heading": c.get("heading", 0),
        "speed": c.get("speed", 0),
        "rpm": c.get("rpm", 0),
        "gear": c.get("gear", 0),
        "throttle": c.get("throttle", 0),
        "brake": c.get("brake", 0),
        "fuel": c.get("fuel", 0),
        "engineTemp": c.get("engineTemp", 0),
        "tireTemps": c.get("tireTemps", {"FL": 0, "FR": 0, "RL": 0, "RR": 0}),
        "tireWear": c.get("tireWear", {"FL": 0, "FR": 0, "RL": 0, "RR": 0}),
        "damage": c.get("damage", 0),
        "compound": c.get("compound", "C3"),
        "drs": c.get("drs", False),
        "ersBattery": c.get("ersBattery", 0),
        "lapTime": c.get("lapTime", 0),
        "bestLapTime": c.get("bestLapTime", 0),
        "stintLaps": c.get("stintLaps", 0),
        "tyreAge": c.get("stintLaps", 0),
        "position": c.get("position", 0),
        "gapToLeader": c.get("gapToLeader", 0),
        "gapToAhead": c.get("gapToAhead", 0),
        "gapToBehind": c.get("gapToBehind", c.get("gapToNext", 0)),
        "gapToNext": c.get("gapToNext", c.get("gapToBehind", 0)),
        "cumulDist": c.get("cumulDist", 0),
        "lapDist": c.get("lapDist", -1),
        "trend": c.get("trend", "→"),
        "currentLap": c.get("currentLap", 0),
        "lastLapTime": c.get("lastLapTime", 0.0),
        "pitStatus": c.get("pitStatus", "NONE"),
        "numPitStops": c.get("numPitStops", 0),
        "driverStatus": c.get("driverStatus", "GARAGE"),
        "resultStatus": c.get("resultStatus", "INVALID"),
        "penaltiesSec": c.get("penaltiesSec", 0),
        "gridPosition": c.get("gridPosition", 0),
        "fiaFlag": c.get("fiaFlag", "UNKNOWN"),
        "visualCompound": c.get("visualCompound", ""),
        "fuelRemainingLaps": c.get("fuelRemainingLaps", 0.0),
        "tyreDamage": c.get("tyreDamage", 0),
        "bodyDamage": c.get("bodyDamage", 0),
        "teamId": c.get("teamId", 255),
        "team": c.get("team", ""),
        "myTeam": c.get("myTeam", False),
        "liveryColour": c.get("liveryColour", ""),
      })

    track_name = self.format.tracks.get(self.track_id, f"Track-{self.track_id}")
    events, self.pending_events = self.pending_events, []

    return {
      "sessionUid": self.session_uid,
      "sessionTime": round(self.session_time, 3),
      "cars": all_cars,
      "track": {
        "id": self.track_id,
        "name": track_name,
        "weather": WEATHER_NAMES.get(self.weather, "Clear"),
        "trackTemp": self.track_temp,
        "airTemp": self.air_temp,
        "totalLaps": self.total_laps,
        "trackLength": self.track_length,
        "sessionType": SESSION_TYPES.get(self.session_type, "UNKNOWN"),
        "safetyCar": SAFETY_CAR_TYPES.get(self.safety_car, "NONE"),
        "rainForecast": list(self.rain_forecast),
        "pitWindow": dict(self.pit_window),
      },
      "player_idx": self.player_idx,
      "lap": self.lap,
      "events": events,
    }
