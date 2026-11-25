"""Ollama provider integration.

Single place where CivicBridge AI talks to the local LLM runtime:

* health / version / tag discovery over the Ollama REST API
* verification that the configured model is actually pulled
* LangChain chat model construction (``langchain-ollama`` preferred, with a
  documented fallback to ``langchain_community``)
* a real smoke test that must return live model output

No hardcoded or canned model responses live here: every failure is raised as
``OllamaUnavailableError`` so callers can degrade gracefully instead of
serving fabricated content.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class OllamaUnavailableError(RuntimeError):
    """Raised when the Ollama service or the configured model is unusable."""


@dataclass
class OllamaModel:
    """A model entry reported by ``GET /api/tags``."""

    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization_level: str = ""
    capabilities: list[str] = field(default_factory=list)

    @property
    def size_human(self) -> str:
        if not self.size_bytes:
            return "unknown"
        return f"{self.size_bytes / 1e9:.1f} GB"

    @property
    def supports_tools(self) -> bool:
        return "tools" in self.capabilities

    @property
    def supports_vision(self) -> bool:
        return "vision" in self.capabilities or "clip" in self.capabilities


@dataclass
class OllamaServiceStatus:
    """Outcome of the Ollama health probe."""

    reachable: bool
    base_url: str
    version: str | None = None
    models: list[OllamaModel] = field(default_factory=list)
    error: str | None = None

    @property
    def model_names(self) -> list[str]:
        return [model.name for model in self.models]


@dataclass
class LlmResponse:
    """Result of a live LLM invocation."""

    text: str
    model: str
    provider: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def normalize_model_name(name: str) -> str:
    """Ollama treats a tag-less name as ``:latest``; normalise for comparison."""
    cleaned = name.strip()
    if not cleaned:
        return cleaned
    return cleaned if ":" in cleaned else f"{cleaned}:latest"


def check_ollama_service(settings: Settings | None = None) -> OllamaServiceStatus:
    """Probe ``/api/version`` and ``/api/tags``. Never raises."""
    cfg = settings or default_settings
    status = OllamaServiceStatus(reachable=False, base_url=cfg.ollama_base_url)

    try:
        with httpx.Client(
            base_url=cfg.ollama_base_url, timeout=cfg.ollama_health_timeout
        ) as client:
            version_response = client.get("/api/version")
            version_response.raise_for_status()
            status.version = version_response.json().get("version")

            tags_response = client.get("/api/tags")
            tags_response.raise_for_status()
            payload = tags_response.json()
    except httpx.ConnectError as exc:
        status.error = f"cannot reach Ollama at {cfg.ollama_base_url} ({exc})"
        logger.warning("Ollama unreachable: %s", status.error)
        return status
    except httpx.HTTPError as exc:
        status.error = f"Ollama HTTP error: {type(exc).__name__}: {exc}"
        logger.warning("Ollama health check failed: %s", status.error)
        return status

    status.reachable = True
    for entry in payload.get("models", []):
        details = entry.get("details") or {}
        status.models.append(
            OllamaModel(
                name=entry.get("name") or entry.get("model", ""),
                size_bytes=int(entry.get("size") or 0),
                parameter_size=details.get("parameter_size", ""),
                quantization_level=details.get("quantization_level", ""),
                capabilities=list(entry.get("capabilities") or []),
            )
        )
    return status


def find_model(status: OllamaServiceStatus, configured_model: str) -> OllamaModel | None:
    """Locate the configured model in the pulled-model list.

    First an exact (tag-normalised) match, then a base-name match, because
    ``llama3.1`` and ``llama3.1:latest`` refer to the same artifact.
    """
    target = normalize_model_name(configured_model)
    for model in status.models:
        if normalize_model_name(model.name) == target:
            return model

    base = configured_model.split(":", 1)[0]
    candidates = [m for m in status.models if m.name.split(":", 1)[0] == base]
    if candidates:
        logger.warning(
            "Configured model %r not found exactly; using %r instead.",
            configured_model,
            candidates[0].name,
        )
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# LangChain integration
# ---------------------------------------------------------------------------
LangChainProvider = tuple[str, type]


def resolve_chat_ollama_class() -> LangChainProvider:
    """Return ``(provider_name, ChatOllama)``, preferring ``langchain-ollama``.

    Raises ``OllamaUnavailableError`` when no integration is importable, which
    the CLI reports as a failed check rather than crashing.
    """
    try:
        module = importlib.import_module("langchain_ollama")
        return "langchain-ollama", module.ChatOllama
    except ImportError as exc:
        logger.warning("langchain-ollama not importable (%s); trying community.", exc)

    try:
        module = importlib.import_module("langchain_community.chat_models")
        return "langchain-community", module.ChatOllama
    except ImportError as exc:
        raise OllamaUnavailableError(
            "No Ollama LangChain integration available. "
            "Install it with: python -m pip install langchain-ollama"
        ) from exc


def get_chat_model(
    *,
    model: str | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
    response_format: str | None = None,
    settings: Settings | None = None,
) -> tuple[Any, str]:
    """Build a configured ``ChatOllama`` instance.

    Returns ``(chat_model, provider_name)``. Model, temperature and token
    limits all come from configuration so swapping the LLM needs no code
    changes. ``response_format="json"`` asks Ollama for JSON-mode output, which
    the agents use for schema-validated structured results.
    """
    cfg = settings or default_settings
    provider, chat_ollama = resolve_chat_ollama_class()

    kwargs: dict[str, Any] = {
        "model": model or cfg.ollama_model,
        "base_url": cfg.ollama_base_url,
        "temperature": cfg.ollama_temperature if temperature is None else temperature,
        "num_predict": cfg.ollama_num_predict if num_predict is None else num_predict,
        "keep_alive": cfg.ollama_keep_alive,
    }
    if cfg.ollama_num_gpu is not None:
        kwargs["num_gpu"] = cfg.ollama_num_gpu
    if response_format:
        kwargs["format"] = response_format
    if provider == "langchain-ollama":
        kwargs["client_kwargs"] = {"timeout": cfg.ollama_llm_timeout}

    try:
        return chat_ollama(**kwargs), provider
    except TypeError as exc:
        # A narrower integration signature: retry with the portable subset.
        logger.warning("Reduced ChatOllama kwargs after TypeError: %s", exc)
        minimal = {
            "model": kwargs["model"],
            "base_url": kwargs["base_url"],
            "temperature": kwargs["temperature"],
            "num_gpu": kwargs["num_gpu"],
        }
        if response_format:
            minimal["format"] = response_format
        return chat_ollama(**minimal), provider


def extract_message_text(content: Any) -> str:
    """Normalise LangChain message content (str or content-block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def run_llm_smoke_test(
    prompt: str,
    *,
    model: str | None = None,
    settings: Settings | None = None,
) -> LlmResponse:
    """Send a real prompt to the configured model and return its live output.

    Raises ``OllamaUnavailableError`` on any transport or generation failure —
    callers must surface that error rather than substituting fake content.
    """
    cfg = settings or default_settings
    chat_model, provider = get_chat_model(model=model, settings=cfg)
    resolved_model = model or cfg.ollama_model

    started = time.perf_counter()
    try:
        message = chat_model.invoke(prompt)
    except Exception as exc:  # normalised into a typed error below
        raise OllamaUnavailableError(
            f"LLM invocation failed for model {resolved_model!r}: {type(exc).__name__}: {exc}"
        ) from exc
    latency_ms = (time.perf_counter() - started) * 1000

    text = extract_message_text(message.content).strip()
    if not text:
        raise OllamaUnavailableError(f"Model {resolved_model!r} returned an empty response.")

    usage = getattr(message, "usage_metadata", None) or {}
    return LlmResponse(
        text=text,
        model=resolved_model,
        provider=provider,
        latency_ms=round(latency_ms, 1),
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
    )
