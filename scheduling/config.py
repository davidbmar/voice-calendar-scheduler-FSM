"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


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

    # RAG
    rag_service_url: str = "http://localhost:8000"

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False

    # WebRTC fallback
    ice_servers_json: str = '[{"urls":"stun:stun.l.google.com:19302"}]'

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


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
