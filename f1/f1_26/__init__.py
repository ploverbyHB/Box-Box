"""
F1 26 season pack — UDP format 2026.

Changes from 2025 that the parser reads: 24 cars in every per-car array, quantised int16
g-forces in motion, uint16 driver/network/team ids in participants, a uint8 engine
temperature in telemetry, the ERS harvest limit added to car status, a severity byte on
collision events, Madrid and the 2026 teams. Session and lap data only grew at the end
or by car count.
"""
import struct

from ..f1_25 import COMPOUNDS as F1_25_COMPOUNDS, DAMAGE, TEAMS as F1_25_TEAMS, TRACKS as F1_25_TRACKS
from ..formats import (
  FORMATS, EventLayout, F1Format, MotionLayout, ParticipantLayout, StatusLayout, TelemetryLayout,
)

TRACKS = {**F1_25_TRACKS, 42: "Madrid"}

COMPOUNDS = F1_25_COMPOUNDS

TEAMS = {
  **F1_25_TEAMS,
  465: "ART GP '25", 466: "Campos '25", 467: "Rodin Motorsport '25", 468: "AIX Racing '25",
  469: "DAMS '25", 470: "Hitech '25", 471: "MP Motorsport '25", 472: "Prema '25",
  473: "Trident '25", 474: "Van Amersfoort Racing '25", 475: "Invicta '25",
  476: "Mercedes '26", 477: "Ferrari '26", 478: "Red Bull Racing '26", 479: "Williams '26",
  480: "Aston Martin '26", 481: "Alpine '26", 482: "RB '26", 483: "Haas '26",
  484: "McLaren '26", 485: "Audi '26", 486: "Cadillac '26",
}

F1_26 = FORMATS.add("2026", F1Format(
  packet_format=2026,
  game="F1 26 season pack",
  num_cars=24,
  motion=MotionLayout(size=54),  # g-forces are int16 (divide by 1000)
  participants=ParticipantLayout(
    size=60,
    team_id="<H",
    team_id_offset=5,  # after aiControlled (uint8), driverId and networkId (uint16)
    my_team_offset=7,
    race_number_offset=8,
    name_offset=10,
    name_len=32,
    livery_offset=47,
  ),
  # engineTemperature is uint8 instead of uint16
  telemetry=TelemetryLayout(car=struct.Struct("<HfffBbHBBH4H4B4BB4f4B")),  # 59 bytes
  status=StatusLayout(size=59),  # m_ersHarvestLimitPerLap inserted after the MGU-H harvest
  damage=DAMAGE,
  events=EventLayout(retirement_reason=True, collision_severity=True),
  tracks=TRACKS,
  compounds=COMPOUNDS,
  teams=TEAMS,
))
