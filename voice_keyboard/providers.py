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

# "none": nothing speaks (a local speech server that only transcribes, such
# as whisper.cpp). Read-aloud says so; Kai shows its answers on screen.
SUPPORTED_TTS_PROVIDERS = {"xai", "hyperfurion", "openai", "elevenlabs", "none"}
NO_VOICE = (
    'Nothing is set up to speak ([tts] provider = "none"): set [tts] to a'
    " speech server or service that speaks (voice-keyboard setup)"
)

DEFAULT_TTS_MODELS = {
    "xai": "",
    "hyperfurion": "",
    "openai": "gpt-4o-mini-tts",
    "elevenlabs": "eleven_multilingual_v2",
    "none": "",
}

DEFAULT_TTS_VOICES = {
    "xai": "eve",
    "hyperfurion": "eve",
    "openai": "coral",
    "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
}
