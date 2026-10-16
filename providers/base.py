"""
Shared shape of every AI provider. Each provider reads <PREFIX>_API_KEY and <PREFIX>_MODEL from the environment.
"""
import os

from registry import Registry

PROVIDERS = Registry("AI provider")


class Provider:
    label = ""
    env_prefix = ""
    model = ""

    def __init__(self):
        self.api_key = os.getenv(f"{self.env_prefix}_API_KEY", "")
        if not self.api_key:
            raise ValueError(f"{self.label} needs {self.env_prefix}_API_KEY in .env")
        self.model = os.getenv(f"{self.env_prefix}_MODEL") or self.model

    @property
    def reasoning(self) -> bool:
        return False

    def chat_json(self, system: str, user: str, schema: dict | None = None, max_tokens: int | None = None,
                  temperature: float | None = None, timeout: float = 60) -> dict:
        """Return the model's reply as a dict; schema is enforced where the provider supports it."""
        raise NotImplementedError
