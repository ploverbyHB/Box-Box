"""
F1 UDP telemetry. Every f1_XX subfolder registers one game's packet format.
"""
from .formats import FORMATS, F1Format
from .parser import F1Accumulator

FORMATS.discover(__name__, __path__)
