"""
AI providers. Every subfolder registers one chat backend; AI_PROVIDER picks which one runs.
"""
from .base import PROVIDERS, Provider

PROVIDERS.discover(__name__, __path__)


def get_provider(name: str) -> Provider:
    return PROVIDERS.get(name.strip().lower())()
