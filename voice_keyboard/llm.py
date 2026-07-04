"""OpenAI-compatible chat client for the voice-transform channel.

Says who: "furion, make that formal" — the instruction and the just-typed
text go to a chat completion, and the rewrite replaces the text in place.
Any OpenAI-compatible /chat/completions endpoint works, including a local
llama.cpp/vLLM server via base_url; xAI Grok is the default.
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URLS = {
    "xai": "https://api.x.ai/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}

DEFAULT_MODELS = {
    "xai": "grok-4-fast",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
}

SYSTEM_PROMPT = (
    "You rewrite dictated text. Apply the user's instruction to the text. "
    "Reply with ONLY the rewritten text: no quotes around it, no commentary, "
    "no markdown fences. Keep the original language and meaning unless the "
    "instruction says otherwise. If the instruction asks a question about "
    "the text rather than requesting a rewrite, answer it in one short "
    "sentence as the output text."
)


def _strip_wrapping(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
        if "\n" in text and text.split("\n", 1)[0].isalpha():
            text = text.split("\n", 1)[1].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        if text[0] not in inner:
            text = inner
    return text


def create_llm_client(config: dict) -> Optional["LLMClient"]:
    """Build the transform client from [llm]; None when not configured."""
    llm_cfg = config.get("llm", {})
    provider = str(llm_cfg.get("provider", "xai")).strip().lower() or "xai"
    base_url = str(llm_cfg.get("base_url", "")).strip()
    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(provider, "")
    if not base_url:
        return None

    api_key = str(llm_cfg.get("api_key", "")).strip()
    if not api_key:
        providers = config.get("providers", {})
        api_key = str(providers.get(provider, {}).get("api_key", "")).strip()
        if provider == "xai" and not api_key:
            api_key = str(config.get("xai", {}).get("api_key", "")).strip()

    model = str(llm_cfg.get("model", "")).strip() or DEFAULT_MODELS.get(provider, "")
    if not model:
        return None
    return LLMClient(base_url=base_url, api_key=api_key, model=model)


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 20.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def rewrite(self, text: str, instruction: str) -> str:
        """Apply `instruction` to `text`; returns the rewritten text.

        Raises RuntimeError with a readable message on any failure —
        callers surface it on the overlay and leave the typed text alone.
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Instruction: {instruction}\n\nText:\n{text}",
                },
            ],
        }
        try:
            response = requests.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except requests.RequestException as exc:
            raise RuntimeError(f"transform request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("transform response had no text") from exc

        rewritten = _strip_wrapping(str(content))
        if not rewritten:
            raise RuntimeError("transform returned empty text")
        return rewritten
