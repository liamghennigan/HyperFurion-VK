"""HyperFurion relay — a metered proxy in front of xAI STT/TTS.

Subscribers get an `hfk_` key instead of a raw provider key. The relay
authenticates the key, enforces per-tier monthly quotas, and forwards
traffic to xAI using the operator's master key. It speaks the exact same
wire protocol as xAI, so the voice-keyboard daemon just points its
`hyperfurion` provider at this host.
"""

__version__ = "0.1.0"
