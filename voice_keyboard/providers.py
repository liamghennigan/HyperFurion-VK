"""Which speech providers exist and their default models — plain data, so
config validation (and every `voice-keyboard` command) can read it without
importing the HTTP and websocket clients behind them."""

SUPPORTED_STT_PROVIDERS = {"xai", "hyperfurion", "openai", "groq", "deepgram", "assemblyai"}

DEFAULT_STT_MODELS = {
    "xai": "",
    "hyperfurion": "",
    "openai": "gpt-4o-transcribe",
    "groq": "whisper-large-v3-turbo",
    "deepgram": "nova-3",
    "assemblyai": "",
}

SUPPORTED_TTS_PROVIDERS = {"xai", "hyperfurion", "openai", "elevenlabs"}

DEFAULT_TTS_MODELS = {
    "xai": "",
    "hyperfurion": "",
    "openai": "gpt-4o-mini-tts",
    "elevenlabs": "eleven_multilingual_v2",
}

DEFAULT_TTS_VOICES = {
    "xai": "eve",
    "hyperfurion": "eve",
    "openai": "coral",
    "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
}
