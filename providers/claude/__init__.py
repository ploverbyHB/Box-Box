"""
Claude, through the Anthropic API. Defaults to Claude Haiku 5.5, the fastest Claude model.

ANTHROPIC_REASONING_EFFORT sets output_config.effort (low, medium, high, xhigh, max); this
provider defaults to low. Thinking is adaptive, so at low effort simple requests skip it.
Haiku 5.5 rejects temperature, so the caller's temperature is not sent.
Keys not scoped to a workspace also need ANTHROPIC_WORKSPACE_ID.
"""
import json
import os

import anthropic

from ..base import PROVIDERS, Provider

# Thinking shares max_tokens with the answer, so the caller's short caps would cut replies off
MAX_TOKENS = 16000


@PROVIDERS.register("claude")
class Claude(Provider):
    label = "Claude"
    env_prefix = "ANTHROPIC"
    model = "claude-haiku-5-5"

    def __init__(self):
        super().__init__()
        self.effort = os.getenv("ANTHROPIC_REASONING_EFFORT", "low").strip().lower()
        workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
        # A retried answer lands after the race has moved on; the next analysis is at most one interval away
        self.client = anthropic.Anthropic(
            api_key=self.api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
            max_retries=0,
            default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None,
        )

    @property
    def reasoning(self) -> bool:
        return True

    def chat_json(self, system: str, user: str, schema: dict | None = None, max_tokens: int | None = None,
                  temperature: float | None = None, timeout: float = 60) -> dict:
        output_config = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            timeout=timeout,
        )
        if resp.stop_reason == "refusal":
            category = resp.stop_details.category if resp.stop_details else None
            raise ValueError(f"Claude declined the request (category={category})")
        text = "".join(block.text for block in resp.content if block.type == "text")
        if not text:
            raise ValueError(f"Claude returned no answer (stop_reason={resp.stop_reason})")
        return json.loads(text)
