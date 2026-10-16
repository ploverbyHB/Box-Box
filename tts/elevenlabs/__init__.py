"""
ElevenLabs, flash model for low-latency pit radio. If ELEVENLABS_VOICE_ID is not in the
account, a premade voice every account has is used instead.
"""
import asyncio
import os

from elevenlabs import ElevenLabs as ElevenLabsClient
from elevenlabs.core.api_error import ApiError
from elevenlabs.types.voice_settings import VoiceSettings

from ..base import TTS_PROVIDERS, TtsProvider

DEFAULT_VOICE_ID = "fxwDjR24F8nuQhDtF5eD"
FALLBACK_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
VOICE_SETTINGS = VoiceSettings(stability=0.4, similarity_boost=0.75, style=0.0, use_speaker_boost=False)


@TTS_PROVIDERS.register("elevenlabs")
class ElevenLabs(TtsProvider):
    label = "ElevenLabs"
    env_prefix = "ELEVENLABS"

    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID
        # Flash is built for low latency (~75 ms) over the multilingual quality model
        self.model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
        self.output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_22050_32")
        # 0-4; higher is lower latency with a slight quality cost
        self.latency_opt = int(os.getenv("ELEVENLABS_LATENCY_OPT", "4"))
        self.client = ElevenLabsClient(api_key=self.api_key) if self.api_key else None

    def missing(self) -> str:
        return "" if self.api_key else "ELEVENLABS_API_KEY is not set"

    def describe(self) -> str:
        return f"{self.label} (voice {self.voice_id})"

    async def audio(self, text: str) -> bytes:
        return await asyncio.get_running_loop().run_in_executor(None, self._speak, text)

    async def voices(self) -> list[dict]:
        def fetch():
            try:
                return [{"id": v.voice_id, "name": v.name} for v in self.client.voices.get_all().voices]
            except Exception:
                return []
        listed = await asyncio.get_running_loop().run_in_executor(None, fetch) if self.client else []
        if not any(v["id"] == self.voice_id for v in listed):
            listed.insert(0, {"id": self.voice_id, "name": self.voice_id})
        return listed

    def _speak(self, text: str) -> bytes:
        try:
            return self._convert(text, self.voice_id)
        except ApiError as e:
            if e.status_code != 404 or self.voice_id == FALLBACK_VOICE_ID:
                raise
            print(f"Voice {self.voice_id} is not in this ElevenLabs account; using premade voice {FALLBACK_VOICE_ID}")
            self.voice_id = FALLBACK_VOICE_ID
            return self._convert(text, self.voice_id)

    def _convert(self, text: str, voice_id: str) -> bytes:
        return b"".join(self.client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id=self.model_id,
            output_format=self.output_format,
            optimize_streaming_latency=self.latency_opt,
            voice_settings=VOICE_SETTINGS,
        ))
