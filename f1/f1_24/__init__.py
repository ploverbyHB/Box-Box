"""
F1 24 — UDP format 2024.
"""
import struct

from ..formats import (
  FORMATS, DamageLayout, EventLayout, F1Format, MotionLayout, ParticipantLayout, StatusLayout,
  TelemetryLayout,
)

TRACKS = {
  0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Bahrain",
  4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
  8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
  12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Austin",
  16: "Interlagos", 17: "Red Bull Ring", 19: "Mexico City", 20: "Baku",
  26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami",
  31: "Las Vegas", 32: "Losail",
}

COMPOUNDS = {
  7: "INTER", 8: "WET",
  9: "DRY", 10: "WET",
  11: "SS", 12: "S", 13: "M", 14: "H", 15: "WET",
  16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0",
  0: "C0", 1: "C1", 2: "C2", 3: "C3", 4: "C4", 5: "C5",
  6: "SOFT",
}

F1_TEAMS = {
  0: "Mercedes", 1: "Ferrari", 2: "Red Bull Racing", 3: "Williams",
  4: "Aston Martin", 5: "Alpine", 6: "RB", 7: "Haas", 8: "McLaren", 9: "Sauber",
  41: "F1 Generic", 104: "F1 Custom Team",
}

TEAMS = {
  **F1_TEAMS,
  143: "ART GP '23", 144: "Campos '23", 145: "Carlin '23", 146: "PHM '23",
  147: "DAMS '23", 148: "Hitech '23", 149: "MP Motorsport '23", 150: "Prema '23",
  151: "Trident '23", 152: "Van Amersfoort Racing '23", 153: "Virtuosi '23",
}

MOTION = MotionLayout(size=60)  # 6 floats, 6 int16 directions, 3 float g-forces, 3 float angles
# CarTelemetryData: speed, throttle, steer, brake, clutch, gear, rpm, drs, revLights %, revLights bits,
# brakesTemperature[4], tyresSurfaceTemperature[4], tyresInnerTemperature[4], engineTemperature (uint16),
# tyresPressure[4], surfaceType[4]
TELEMETRY = TelemetryLayout(car=struct.Struct("<HfffBbHBBH4H4B4BH4f4B"))  # 60 bytes
STATUS = StatusLayout(size=55)

F1_24 = FORMATS.add("2024", F1Format(
  packet_format=2024,
  game="F1 24",
  num_cars=22,
  motion=MOTION,
  participants=ParticipantLayout(
    size=60,
    team_id="B",
    team_id_offset=3,  # after aiControlled, driverId, networkId (uint8 each)
    my_team_offset=4,
    race_number_offset=5,
    name_offset=7,
    name_len=48,
    livery_offset=None,
  ),
  telemetry=TELEMETRY,
  status=STATUS,
  damage=DamageLayout(
    size=42,
    front_wing=24,  # after tyresWear[4] floats, tyresDamage[4], brakesDamage[4]
  ),
  events=EventLayout(retirement_reason=False, collision_severity=False),
  tracks=TRACKS,
  compounds=COMPOUNDS,
  teams=TEAMS,
))
