"""OpenAI-compatible chat client for the voice-transform channel.

Says who: "vk, make that formal" — the instruction and the just-typed
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
    "xai": "grok-4.3",
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


# Punctuation where the speaker paused (flow/pauses.py). The reply is just
# the two words around the pause, so a wrong answer is easy to reject.
PAUSE_REVIEW_SYSTEM_PROMPT = (
    "You fix punctuation in live dictation. The speech recognizer ends a "
    "sentence wherever the speaker pauses: it puts a period at the end of "
    "BEFORE and a capital letter at the start of AFTER, even when the "
    "speaker only paused mid-sentence to think. Decide what really belongs "
    "at that pause. Reply with ONLY the last word of BEFORE and the first "
    "word of AFTER, with the punctuation that belongs between them "
    "(nothing, a comma, a period, a question mark) and correct "
    "capitalization. No other text.\n\n"
    "before: So I was thinking about the project.\n"
    "after: And how we could make it simpler\n"
    "reply: project and\n\n"
    "before: The meeting moved to Friday.\n"
    "after: Let's get the slides ready\n"
    "reply: Friday. Let's\n\n"
    "before: I talked to.\n"
    "after: Sarah about the budget\n"
    "reply: to Sarah\n\n"
    "before: We could ship it today.\n"
    "after: But the tests are failing\n"
    "reply: today, but\n\n"
    "before: I think.\n"
    "after: We should wait until Monday\n"
    "reply: think we"
)


ASK_SYSTEM_PROMPT = (
    "You answer a spoken question, usually about the provided text "
    "(selected on the user's screen). Reply with ONLY the answer: plain "
    "prose, no markdown, no preamble, at most three sentences unless the "
    "question demands more. If there is no text, answer the question "
    "directly."
)

# Small models follow EXAMPLES far better than instructions. These
# few-shot prompts let a ~1B local model do the terminal command work
# reliably; a zero-shot instruction prompt does not. The user turn is
# always "words: <request>\ncommand:" so the model completes one command.

# Routing (terminal focused): a command, or NONE when it's a question.
ROUTE_SYSTEM_PROMPT = (
    "Convert the user's spoken words into a single shell command line. "
    "If the words are a question, a fact request, an explanation, or "
    "anything not meant to run in a shell, output exactly NONE. Output "
    "ONLY the command (or NONE) — no prose, no markdown, no leading $.\n\n"
    "words: show me the git status\n"
    "command: git status\n"
    "words: what is the capital of france\n"
    "command: NONE\n"
    "words: delete all the pyc files\n"
    "command: find . -name '*.pyc' -delete\n"
    "words: who won the world series\n"
    "command: NONE\n"
    "words: undo my last commit but keep the changes\n"
    "command: git reset --soft HEAD~1\n"
    "words: list the biggest files here\n"
    "command: du -ah . | sort -rh | head\n"
    "words: explain what a race condition is\n"
    "command: NONE\n"
    "words: make a new branch called test and switch to it\n"
    "command: git checkout -b test"
)

# Explicit intent ("vk, run …"): always a command, never NONE.
INTENT_SYSTEM_PROMPT = (
    "Convert the user's spoken request into a single shell command line. "
    "Output ONLY the command — no prose, no markdown, no leading $. "
    "Prefer safe, read-only forms unless clearly asked otherwise.\n\n"
    "words: list files sorted by size\n"
    "command: ls -lS\n"
    "words: find every todo in this repo\n"
    "command: grep -rn TODO .\n"
    "words: show running docker containers\n"
    "command: docker ps\n"
    "words: undo my last commit but keep the changes\n"
    "command: git reset --soft HEAD~1\n"
    "words: make a new branch called test and switch to it\n"
    "command: git checkout -b test"
)


def _command_from_reply(reply: str) -> Optional[str]:
    """Extract a single command line from a few-shot completion. NONE,
    empty, or prose all yield None so nothing junk reaches the prompt."""
    lines = _strip_wrapping(reply).splitlines() if reply else []
    line = lines[0].strip().strip("`").lstrip("$").strip() if lines else ""
    if not line or line.upper() == "NONE" or _looks_like_prose(line):
        return None
    return line


def _looks_like_prose(command: str) -> bool:
    """A last-ditch guard: a 'command' that is really a sentence or a
    model's inline answer should not be typed at a shell prompt."""
    if "\\boxed" in command or command.endswith("?"):
        return True
    # A natural-language clarifying question ("Which directory …?").
    lowered = command.lower()
    return lowered.startswith(("which ", "what ", "do you ", "would you ", "sorry"))


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


def _llm_settings(config: dict) -> tuple[str, str, str]:
    """[llm]'s (base_url, api_key, model), defaults and key fallbacks applied."""
    llm_cfg = config.get("llm", {})
    provider = str(llm_cfg.get("provider", "xai")).strip().lower() or "xai"
    base_url = str(llm_cfg.get("base_url", "")).strip() or DEFAULT_BASE_URLS.get(provider, "")
    api_key = str(llm_cfg.get("api_key", "")).strip()
    if not api_key:
        providers = config.get("providers", {})
        api_key = str(providers.get(provider, {}).get("api_key", "")).strip()
        if provider == "xai" and not api_key:
            api_key = str(config.get("xai", {}).get("api_key", "")).strip()
    model = str(llm_cfg.get("model", "")).strip() or DEFAULT_MODELS.get(provider, "")
    return base_url, api_key, model


def llm_ready(config: dict) -> bool:
    """True when [llm] can answer without asking: a model and an endpoint,
    and a real key unless the endpoint is local."""
    from voice_keyboard.config import PLACEHOLDER_API_KEYS, _is_local_endpoint

    base_url, api_key, model = _llm_settings(config)
    if not base_url or not model:
        return False
    return _is_local_endpoint(base_url) or (
        bool(api_key) and api_key not in PLACEHOLDER_API_KEYS
    )


def create_llm_client(config: dict) -> Optional["LLMClient"]:
    """Build the transform client from [llm]; None when not configured."""
    base_url, api_key, model = _llm_settings(config)
    if not base_url or not model:
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

    def answer(self, question: str, context: str = "") -> str:
        """Answer a question, optionally about selected text.

        Raises RuntimeError with a readable message on any failure,
        like `rewrite`.
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if context:
            user = f"Question: {question}\n\nText:\n{context}"
        else:
            user = f"Question: {question}"
        payload = {
            "model": self._model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {"role": "user", "content": user},
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
            raise RuntimeError(f"ask request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("ask response had no text") from exc

        text = _strip_wrapping(str(content))
        if not text:
            raise RuntimeError("ask returned empty text")
        return text

    def complete(self, prompt: str) -> str:
        """A single-turn completion of a fully-built prompt — the local
        brain path for the assistant. Raises RuntimeError on failure."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
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
            raise RuntimeError(f"assistant request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("assistant response had no text") from exc
        text = str(content).strip()
        if not text:
            raise RuntimeError("assistant returned empty text")
        return text

    def route_terminal_request(self, request: str) -> Optional[str]:
        """Classify+compile a spoken query made with a terminal focused, in
        ONE call: return the shell command to type, or None when it's a
        question (the caller answers it aloud instead of typing at the
        prompt). Few-shot, so a small local model handles it. Default-safe:
        NONE / empty / prose all yield None."""
        reply = self._chat(
            ROUTE_SYSTEM_PROMPT, f"words: {request}\ncommand:", temperature=0.0
        )
        return _command_from_reply(reply)

    def review_pause(self, before: str, after: str) -> str:
        """The two words around a dictation pause, punctuated and capitalized
        as they should be (see PAUSE_REVIEW_SYSTEM_PROMPT); the caller
        checks the reply. Raises RuntimeError on failure."""
        return self._chat(
            PAUSE_REVIEW_SYSTEM_PROMPT,
            f"before: {before}\nafter: {after}\nreply:",
            temperature=0.0,
            max_tokens=24,
            timeout=min(self._timeout, 5.0),
        )

    def _chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        try:
            response = requests.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout or self._timeout,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except requests.RequestException as exc:
            raise RuntimeError(f"request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("response had no text") from exc
        return str(content)

    def compile_command(self, request: str) -> str:
        """Turn a spoken request into ONE command line (never executed here).

        Few-shot so a small local model handles it. Raises RuntimeError on
        failure. The injector's no-Enter mode is the real guarantee; this
        keeps the typed text a single sane line.
        """
        reply = self._chat(
            INTENT_SYSTEM_PROMPT, f"words: {request}\ncommand:", temperature=0.0
        )
        command = _command_from_reply(reply)
        if not command:
            raise RuntimeError("intent produced no command")
        return command
