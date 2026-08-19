# Archive

Code that is **not** a current product path. It is parked here so the
history stays obvious. Nothing in this directory is an install, launch,
or subscription surface.

## `relay/`

A metered hosted-subscription relay (Stripe, email login, demo STT/TTS
on the operator’s xAI key, `api.hyperfurion.com`). **It was never
launched and is not active.**

Do not deploy it as the product backend. Do not point the landing page,
installers, or `voice-keyboard login` at it. HyperFurion VK is
bring-your-own-key — or a local OpenAI-compatible server — and fully
usable offline.

The tree is still self-hostable if you want to read or experiment with
it. That is operator documentation for an unpublished service, not a
user guide. The default test suite does not collect these tests.

```bash
# optional, only if you are working on the archived relay
cd archive/relay
pip install .
python3 -m pytest tests -q
```
