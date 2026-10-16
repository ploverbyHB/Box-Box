"""
F1 UDP formats. Each f1_XX folder registers the per-car layouts and id tables of one game.
Only what differs between formats lives here; session and lap data payloads are identical
in every registered format and stay as constants in parser.py.
"""
import struct
from dataclasses import dataclass

from registry import Registry

FORMATS = Registry("F1 UDP format")


@dataclass(frozen=True)
class MotionLayout:
  size: int  # bytes per CarMotionData; position X/Z at 0/8, forward dir X/Z (int16) at 24/28


@dataclass(frozen=True)
class ParticipantLayout:
  size: int  # bytes per ParticipantData
  team_id: str  # struct code of m_teamId
  team_id_offset: int
  my_team_offset: int
  race_number_offset: int
  name_offset: int
  name_len: int  # m_name length
  livery_offset: int | None  # m_numColours; None = no livery colours


@dataclass(frozen=True)
class TelemetryLayout:
  car: struct.Struct  # CarTelemetryData; the parser reads fields by index, which every format keeps


@dataclass(frozen=True)
class StatusLayout:
  size: int  # bytes per CarStatusData; the fields read sit before any format's additions


@dataclass(frozen=True)
class DamageLayout:
  size: int  # bytes per CarDamageData
  front_wing: int  # m_frontLeftWingDamage offset; rear wing, floor, diffuser, sidepod follow the right wing


@dataclass(frozen=True)
class EventLayout:
  retirement_reason: bool  # RTMT carries a reason byte after vehicleIdx
  collision_severity: bool  # COLL carries a severity byte after the two vehicle indices


@dataclass(frozen=True)
class F1Format:
  packet_format: int  # m_packetFormat in the header, also the key in FORMATS
  game: str
  num_cars: int  # entries in every per-car array
  motion: MotionLayout
  participants: ParticipantLayout
  telemetry: TelemetryLayout
  status: StatusLayout
  damage: DamageLayout
  events: EventLayout
  tracks: dict[int, str]
  compounds: dict[int, str]  # m_actualTyreCompound ids
  teams: dict[int, str]  # m_teamId ids
