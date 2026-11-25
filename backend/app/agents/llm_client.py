"""The single place the agents talk to the local language model.

Design constraints, all driven by the measured ~19 s CPU inference cost:

* **One model instance per process.** Building ``ChatOllama`` is cheap but not
  free, and keeping one instance lets Ollama reuse the loaded weights
  (``OLLAMA_KEEP_ALIVE``).
* **Bounded response cache.** Analysing the same complaint text twice must not
  cost two inferences.
* **JSON mode.** Every call sets ``format="json"`` so the 8B model is
  constrained towards parseable output; the agents still validate with Pydantic
  and repair once if needed.
* **Availability is probed, never assumed.** ``is_available()`` performs a real
  ``/api/tags`` probe (memoised briefly) so a missing model can never be
  mistaken for a working one.

The client is deliberately dumb: it returns text and metadata. Schema
validation, repair prompting and fallback decisions live in the agents, which
keeps them testable with a fake client.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Protocol

from app.agents.errors import AgentLlmError
from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger
from app.services.ollama_service import (
    check_ollama_service,
    extract_message_text,
    find_model,
    get_chat_model,
)

logger = get_logger(__name__)

#: How long an availability probe result is reused before re-probing.
AVAILABILITY_TTL_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class LlmCallResult:
    """One model response plus the metadata the agent logs need."""

    text: str
    model: str
    provider: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached: bool = False


class LlmClient(Protocol):
    """Minimal surface the agents depend on (easy to fake in unit tests)."""

    def is_available(self) -> bool:
        """Whether a real inference call can be expected to succeed."""

    def complete(self, *, system: str, user: str) -> LlmCallResult:
        """Run one completion; raise :class:`AgentLlmError` on any failure."""


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

_response_cache: OrderedDict[str, LlmCallResult] = OrderedDict()


def _cache_key(model: str, system: str, user: str) -> str:
    digest = hashlib.sha256(f"{model}\x00{system}\x00{user}".encode())
    return digest.hexdigest()


def clear_llm_cache() -> None:
    """Drop every cached response (used by tests and by the CLI)."""
    _response_cache.clear()


def llm_cache_size() -> int:
    return len(_response_cache)


def _cache_get(key: str) -> LlmCallResult | None:
    result = _response_cache.get(key)
    if result is None:
        return None
    _response_cache.move_to_end(key)
    return replace(result, cached=True)


def _cache_put(key: str, result: LlmCallResult, limit: int) -> None:
    if limit <= 0:
        return
    _response_cache[key] = result
    _response_cache.move_to_end(key)
    while len(_response_cache) > limit:
        _response_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Ollama-backed client
# ---------------------------------------------------------------------------


class OllamaLlmClient:
    """``ChatOllama`` wrapper configured for structured agent output."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        response_format: str | None = "json",
        num_predict: int | None = None,
        use_cache: bool = True,
    ) -> None:
        self._settings = settings or default_settings
        self._response_format = response_format
        self._num_predict = max(
            num_predict or self._settings.agent_num_predict,
            self._settings.ollama_num_predict,
        )
        self._use_cache = use_cache
        self._chat_model: Any | None = None
        self._provider = ""
        self._available: bool | None = None
        self._available_at = 0.0

    # -- availability ------------------------------------------------------

    def is_available(self) -> bool:
        """Probe Ollama and the configured model, memoised for a short TTL."""
        now = time.monotonic()
        if self._available is not None and (now - self._available_at) < AVAILABILITY_TTL_SECONDS:
            return self._available

        status = check_ollama_service(self._settings)
        available = status.reachable and find_model(status, self._settings.ollama_model) is not None
        if not available:
            logger.warning(
                "LLM unavailable (%s); agents will use the deterministic fallback.",
                status.error or f"model {self._settings.ollama_model!r} not pulled",
            )

        self._available = available
        self._available_at = now
        return available

    def refresh_availability(self) -> bool:
        """Force a fresh probe (used after an operator starts Ollama)."""
        self._available = None
        return self.is_available()

    # -- invocation --------------------------------------------------------

    def _ensure_chat_model(self) -> Any:
        if self._chat_model is None:
            self._chat_model, self._provider = get_chat_model(
                num_predict=self._num_predict,
                response_format=self._response_format,
                settings=self._settings,
            )
        return self._chat_model

    def complete(self, *, system: str, user: str) -> LlmCallResult:
        """Run one JSON-mode completion.

        Raises :class:`AgentLlmError` when the model cannot be reached, returns
        nothing usable, or overruns ``OLLAMA_LLM_TIMEOUT``.
        """
        model_name = self._settings.ollama_model
        key = _cache_key(f"{model_name}|{self._num_predict}", system, user)
        if self._use_cache:
            cached = _cache_get(key)
            if cached is not None:
                logger.debug("LLM response served from cache (model=%s)", model_name)
                return cached

        chat_model = self._ensure_chat_model()
        messages = self._build_messages(system, user)

        started = time.perf_counter()
        try:
            message = chat_model.invoke(messages)
        except Exception as exc:
            raise AgentLlmError(
                f"LLM invocation failed for model {model_name!r}: {type(exc).__name__}: {exc}"
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        text = extract_message_text(message.content).strip()
        if not text:
            raise AgentLlmError(f"model {model_name!r} returned an empty response")

        usage = getattr(message, "usage_metadata", None) or {}
        result = LlmCallResult(
            text=text,
            model=model_name,
            provider=self._provider,
            latency_ms=round(latency_ms, 1),
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
        )
        if self._use_cache:
            _cache_put(key, result, self._settings.agent_response_cache_size)
        return result

    @staticmethod
    def _build_messages(system: str, user: str) -> list[Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        return [SystemMessage(content=system), HumanMessage(content=user)]


@lru_cache(maxsize=1)
def get_default_llm_client() -> OllamaLlmClient:
    """Process-wide client so the loaded model and cache are shared."""
    return OllamaLlmClient()
