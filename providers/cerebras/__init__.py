"""
Cerebras. CEREBRAS_REASONING_EFFORT is sent as reasoning_effort: none, low, medium or high.
gpt-oss-120b accepts low, medium and high only.
"""
import os

from ..base import PROVIDERS
from ..openai_compatible import OpenAICompatibleProvider


@PROVIDERS.register("cerebras")
class Cerebras(OpenAICompatibleProvider):
    label = "Cerebras"
    env_prefix = "CEREBRAS"
    base_url = "https://api.cerebras.ai/v1"
    model = "qwen-3.8-27b"

    def __init__(self):
        super().__init__()
        self.reasoning_effort = os.getenv("CEREBRAS_REASONING_EFFORT", "none").strip().lower()

    @property
    def reasoning(self) -> bool:
        return self.reasoning_effort != "none"

    def request_options(self) -> dict:
        return {"reasoning_effort": self.reasoning_effort}
