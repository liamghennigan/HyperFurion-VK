"""Total recall: search everything you ever dictated.

The opt-in ledger is a memory; this makes it queryable. Keyword scoring
works with zero setup. Point [recall] base_url at an OpenAI-compatible
/embeddings endpoint — a local Ollama (http://localhost:11434/v1) keeps
it fully on-box — and search becomes semantic. On any embedding failure
the keyword path answers instead: recall degrades, never breaks.
"""

import logging
import math
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)
EMBED_TIMEOUT_S = 30.0
MAX_EMBED_ENTRIES = 200


def create_embedder(config: dict) -> Optional["Embedder"]:
    recall_cfg = config.get("recall", {})
    base_url = str(recall_cfg.get("base_url", "")).strip()
    model = str(recall_cfg.get("model", "")).strip()
    if not base_url or not model:
        return None
    return Embedder(
        base_url=base_url,
        model=model,
        api_key=str(recall_cfg.get("api_key", "")).strip(),
    )


class Embedder:
    def __init__(self, *, base_url: str, model: str, api_key: str = ""):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = requests.post(
                f"{self._base_url}/embeddings",
                headers=headers,
                json={"model": self._model, "input": texts},
                timeout=EMBED_TIMEOUT_S,
            )
            response.raise_for_status()
            data = response.json()["data"]
            vectors = [item["embedding"] for item in data]
        except requests.RequestException as exc:
            raise RuntimeError(f"embedding request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("embedding response had no vectors") from exc
        if len(vectors) != len(texts):
            raise RuntimeError("embedding response count mismatch")
        return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _keyword_score(query_tokens: list[str], text: str) -> float:
    text_lower = text.casefold()
    text_tokens = {t.casefold() for t in _WORD_RE.findall(text)}
    if not query_tokens:
        return 0.0
    overlap = sum(1 for token in query_tokens if token in text_tokens)
    score = overlap / len(query_tokens)
    if " ".join(query_tokens) in text_lower:
        score += 0.5
    return score


def search(
    entries: list[dict],
    query: str,
    *,
    embedder: Optional[Embedder] = None,
    limit: int = 5,
) -> list[dict]:
    """Best ledger entries for a query, newest-first on ties.

    Returns copies of the entries with a `score` field added; entries
    from the intent channel are commands, not speech, and are skipped.
    """
    candidates = [
        entry
        for entry in entries
        if str(entry.get("text", "")).strip()
        and str(entry.get("register", "")) not in {"intent", "macro", "ask"}
    ]
    if not candidates:
        return []
    candidates = candidates[-MAX_EMBED_ENTRIES:]

    scores: Optional[list[float]] = None
    if embedder is not None:
        try:
            vectors = embedder.embed(
                [query] + [str(entry["text"]) for entry in candidates]
            )
            query_vec = vectors[0]
            scores = [_cosine(query_vec, vec) for vec in vectors[1:]]
        except Exception as exc:
            logger.warning("Semantic recall unavailable (%s); keyword search", exc)
            scores = None
    if scores is None:
        query_tokens = [t.casefold() for t in _WORD_RE.findall(query)]
        scores = [
            _keyword_score(query_tokens, str(entry["text"])) for entry in candidates
        ]

    ranked = sorted(
        zip(candidates, scores),
        key=lambda pair: (-pair[1], -float(pair[0].get("ts", 0))),
    )
    results = []
    for entry, score in ranked[: max(1, limit)]:
        if score <= 0:
            continue
        hit = dict(entry)
        hit["score"] = round(float(score), 4)
        results.append(hit)
    return results
