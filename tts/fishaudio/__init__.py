"""
Fish Audio. FISH_AUDIO_REFERENCE_ID picks the voice model from fish.audio and FISH_AUDIO_VOICES
lists more model ids for the dashboard's voice picker. FISH_AUDIO_MODEL and FISH_AUDIO_LATENCY
("low", "balanced", "normal") trade speed against quality.
"""
import os

import aiohttp

from ..base import TTS_PROVIDERS, TtsProvider

URL = "https://api.fish.audio/v1/tts"
MODEL_URL = "https://api.fish.audio/model/{}"
TIMEOUT_S = 30


@TTS_PROVIDERS.register("fishaudio")
class FishAudio(TtsProvider):
    label = "Fish Audio"
    env_prefix = "FISH_AUDIO"

    def __init__(self):
        self.api_key = os.getenv("FISH_AUDIO_API_KEY", "")
        self.voice_id = os.getenv("FISH_AUDIO_REFERENCE_ID", "").strip()
        self.listed = [v.strip() for v in os.getenv("FISH_AUDIO_VOICES", "").split(",") if v.strip()]
        self.names = {}
        self.model = os.getenv("FISH_AUDIO_MODEL", "s2.1-pro")
        self.latency = os.getenv("FISH_AUDIO_LATENCY", "normal")

    def missing(self) -> str:
        return "" if self.api_key else "FISH_AUDIO_API_KEY is not set"

    def describe(self) -> str:
        return f"{self.label} ({self.model}, voice {self.voice_id or 'default'})"

    async def audio(self, text: str) -> bytes:
        body = {"text": text, "format": "mp3", "mp3_bitrate": 64, "latency": self.latency}
        if self.voice_id:
            body["reference_id"] = self.voice_id
        headers = {"Authorization": f"Bearer {self.api_key}", "model": self.model}
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(URL, json=body, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                return await resp.read()

    async def voices(self) -> list[dict]:
        ids = list(dict.fromkeys(([self.voice_id] if self.voice_id else []) + self.listed))
        return [{"id": i, "name": await self.name_of(i)} for i in ids]

    async def name_of(self, voice_id: str) -> str:
        if voice_id not in self.names:
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(MODEL_URL.format(voice_id), headers=headers) as resp:
                        self.names[voice_id] = ((await resp.json()).get("title") or "").strip() if resp.status == 200 else ""
            except Exception:
                self.names[voice_id] = ""
        return self.names[voice_id] or voice_id

    def speaker_name(self) -> str:
        return self.names.get(self.voice_id, "")
