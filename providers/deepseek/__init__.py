"""
DeepSeek. DEEPSEEK_REASONING_EFFORT sets V4 thinking: none (off), low, high or max.
The API default is high; this provider defaults to low.
"""
import os

from ..base import PROVIDERS
from ..openai_compatible import OpenAICompatibleProvider

REASONING_EFFORTS = ("none", "low", "high", "max")


@PROVIDERS.register("deepseek")
class DeepSeek(OpenAICompatibleProvider):
    label = "DeepSeek"
    env_prefix = "DEEPSEEK"
    base_url = "https://api.deepseek.com"
    model = "deepseek-v4-flash"

    def __init__(self):
        super().__init__()
        self.reasoning_effort = os.getenv("DEEPSEEK_REASONING_EFFORT", "low").strip().lower()
        if self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(
                f"DEEPSEEK_REASONING_EFFORT must be one of {', '.join(REASONING_EFFORTS)}, "
                f"got {self.reasoning_effort!r}"
            )

    @property
    def reasoning(self) -> bool:
        return self.reasoning_effort != "none"

    def request_options(self) -> dict:
        if not self.reasoning:
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {
            "reasoning_effort": self.reasoning_effort,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
