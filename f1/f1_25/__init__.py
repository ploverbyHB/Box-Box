"""
F1 25 — UDP format 2025.

Changes from 2024 that the parser reads: participant names cut to 32 chars with
4 RGB livery colours appended, tyreBlisters[4] inserted after brakesDamage[4] in
car damage, a reason byte on the retirement event, the C6 compound, reverse
track layouts and a new team list (the F2 '23 teams are gone).
"""
from ..f1_24 import (
  COMPOUNDS as F1_24_COMPOUNDS, F1_TEAMS, MOTION, STATUS, TELEMETRY, TRACKS as F1_24_TRACKS,
)
from ..formats import FORMATS, DamageLayout, EventLayout, F1Format, ParticipantLayout

TRACKS = {
  **F1_24_TRACKS,
  39: "Silverstone Reverse", 40: "Red Bull Ring Reverse", 41: "Zandvoort Reverse",
}

COMPOUNDS = {**F1_24_COMPOUNDS, 22: "C6"}

TEAMS = {
  **F1_TEAMS,
  129: "Konnersport", 142: "APXGP '24", 154: "APXGP '25", 155: "Konnersport '24",
  158: "Art GP '24", 159: "Campos '24", 160: "Rodin Motorsport '24", 161: "AIX Racing '24",
  162: "DAMS '24", 163: "Hitech '24", 164: "MP Motorsport '24", 165: "Prema '24",
  166: "Trident '24", 167: "Van Amersfoort Racing '24", 168: "Invicta '24",
  185: "Mercedes '24", 186: "Ferrari '24", 187: "Red Bull Racing '24", 188: "Williams '24",
  189: "Aston Martin '24", 190: "Alpine '24", 191: "RB '24", 192: "Haas '24",
  193: "McLaren '24", 194: "Sauber '24",
}

DAMAGE = DamageLayout(size=46, front_wing=28)

F1_25 = FORMATS.add("2025", F1Format(
  packet_format=2025,
  game="F1 25",
  num_cars=22,
  motion=MOTION,
  participants=ParticipantLayout(
    size=57,
    team_id="B",
    team_id_offset=3,
    my_team_offset=4,
    race_number_offset=5,
    name_offset=7,
    name_len=32,
    livery_offset=44,  # after the 32-char name, telemetry flags, techLevel, platform
  ),
  telemetry=TELEMETRY,
  status=STATUS,
  damage=DAMAGE,
  events=EventLayout(retirement_reason=True, collision_severity=False),
  tracks=TRACKS,
  compounds=COMPOUNDS,
  teams=TEAMS,
))
