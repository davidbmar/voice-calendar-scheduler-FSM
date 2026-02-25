"""Application configuration via environment variables."""

from __future__ import annotations

import logging

from pydantic_settings import BaseSettings

log = logging.getLogger("scheduling.config")


class Settings(BaseSettings):
    # LLM
    llm_provider: str = "claude"
    anthropic_api_key: str = ""
    ollama_model: str = "qwen2.5:7b"
    ollama_url: str = "http://localhost:11434"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Google Calendar
    google_service_account_json: str = ""
    google_calendar_id: str = "primary"
    calendar_timezone: str = "America/Chicago"

    # RAG
    rag_service_url: str = "http://localhost:8000"

    # Admin auth
    admin_api_key: str = ""

    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    debug: bool = False

    # WebRTC fallback
    ice_servers_json: str = '[{"urls":"stun:stun.l.google.com:19302"}]'

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def validate_startup(self) -> list[str]:
        """Validate configuration at startup. Returns warnings, raises on errors."""
        warnings: list[str] = []
        _placeholders = {"sk-ant-...", "AC...", "path/to/service-account.json"}

        # LLM key — required
        if self.llm_provider == "claude":
            if not self.anthropic_api_key or self.anthropic_api_key in _placeholders:
                raise ValueError(
                    "ANTHROPIC_API_KEY is missing or still a placeholder. "
                    "Set it in .env to use Claude."
                )

        # Admin API key — warn if unset
        if not self.admin_api_key:
            if self.debug:
                warnings.append(
                    "ADMIN_API_KEY not set. Admin APIs are open (DEBUG=true)."
                )
            else:
                warnings.append(
                    "ADMIN_API_KEY not set. Admin APIs are locked in production. "
                    "Set ADMIN_API_KEY in .env to enable admin access."
                )

        # Twilio — warn if placeholder
        if self.twilio_account_sid in _placeholders:
            warnings.append("TWILIO_ACCOUNT_SID is a placeholder — Twilio calls won't work.")

        # Google Calendar — warn if placeholder
        if self.google_service_account_json in _placeholders:
            warnings.append(
                "GOOGLE_SERVICE_ACCOUNT_JSON is a placeholder — calendar integration disabled."
            )

        return warnings


settings = Settings()

# Runtime-mutable settings (admin API can change these)
runtime_settings = {
    "barge_in_enabled": True,
    "tts_voice": "af_heart",
    "tts_engine": "kokoro",  # "kokoro" or "piper"
    # VAD (normal listening): low threshold — user is speaking directly into mic
    "vad_energy_threshold": 300,
    # VAD: 1 frame to confirm speech start (respond quickly)
    "vad_speech_confirm_frames": 1,
    # VAD: silence frames after speech to trigger transcription (8 = ~0.8s)
    "vad_silence_gap": 8,
    # Barge-in (during TTS playback): threshold to detect user speech over echo.
    # Browser echoCancellation handles most TTS echo (~200-600 after AEC).
    # Human speech typically 800-3000+, so 600 works well with echo cancellation active.
    "barge_in_energy_threshold": 600,
    # Barge-in: 2 consecutive polls (~200ms) to confirm — responsive to short phrases.
    "barge_in_confirm_frames": 2,
}
