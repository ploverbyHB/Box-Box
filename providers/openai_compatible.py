"""
Chat backend for providers that speak the OpenAI chat completions API. Adds <PREFIX>_BASE_URL.
"""
import json
import os

from openai import OpenAI

from .base import Provider


class OpenAICompatibleProvider(Provider):
    base_url = ""

    def __init__(self):
        super().__init__()
        # A retried answer lands after the race has moved on; the next analysis is at most one interval away
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=os.getenv(f"{self.env_prefix}_BASE_URL") or self.base_url,
            max_retries=0,
        )

    def request_options(self) -> dict:
        return {}

    def chat_json(self, system: str, user: str, schema: dict | None = None, max_tokens: int | None = None,
                  temperature: float | None = None, timeout: float = 60) -> dict:
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "timeout": timeout,
            **self.request_options(),
        }
        # Reasoning tokens count against max_tokens, so a short cap leaves nothing for the answer
        if max_tokens and not self.reasoning:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        if not choice.message.content:
            raise ValueError(f"{self.label} returned no answer (finish_reason={choice.finish_reason})")
        return json.loads(choice.message.content)
