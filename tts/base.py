"""
Shared shape of every speech provider: turn one line of text into audio bytes.
"""
from registry import Registry

TTS_PROVIDERS = Registry("TTS provider")


class TtsProvider:
    label = ""
    env_prefix = ""
    extension = "mp3"
    voice_id = ""

    def missing(self) -> str:
        """Why the provider cannot speak (an unset key), or "" when it can."""
        return ""

    def describe(self) -> str:
        return self.label

    async def audio(self, text: str) -> bytes:
        raise NotImplementedError

    async def voices(self) -> list[dict]:
        """Voices the dashboard can switch between: [{"id", "name"}]."""
        return [{"id": self.voice_id, "name": self.voice_id}] if self.voice_id else []

    def set_voice(self, voice_id: str):
        self.voice_id = voice_id

    def speaker_name(self) -> str:
        """The person the current voice belongs to, when the provider knows one."""
        return ""
