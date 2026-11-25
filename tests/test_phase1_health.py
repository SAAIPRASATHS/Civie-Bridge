"""Phase 1 infrastructure tests.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase1_health.py -v

Tests are split into:
* unit tests - no running services required, always executed
* integration tests - skipped automatically when PostgreSQL or Ollama is down
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from app.database.session import check_database
from app.services.ollama_service import (
    OllamaUnavailableError,
    check_ollama_service,
    find_model,
    normalize_model_name,
    resolve_chat_ollama_class,
    run_llm_smoke_test,
)

# ---------------------------------------------------------------------------
# Unit tests - no external services
# ---------------------------------------------------------------------------


def test_settings_load_from_env() -> None:
    assert settings.ollama_model, "OLLAMA_MODEL must be configured"
    assert settings.ollama_base_url.startswith("http")
    assert "en" in settings.languages and "ta" in settings.languages


def test_database_url_password_masking() -> None:
    assert settings.postgres_password not in settings.safe_database_url
    assert "***" in settings.safe_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("llama3.1:latest", "llama3.1:latest"),
        ("llama3.1", "llama3.1:latest"),
        ("qwen2.5:3b", "qwen2.5:3b"),
        ("  qwen2.5  ", "qwen2.5:latest"),
    ],
)
def test_normalize_model_name(raw: str, expected: str) -> None:
    assert normalize_model_name(raw) == expected


def test_find_model_prefers_exact_match() -> None:
    from app.services.ollama_service import OllamaModel, OllamaServiceStatus

    status = OllamaServiceStatus(
        reachable=True,
        base_url="http://localhost:11434",
        models=[
            OllamaModel(name="llama3.1:8b-instruct-q4_K_M"),
            OllamaModel(name="llama3.1:latest"),
        ],
    )
    match = find_model(status, "llama3.1:latest")
    assert match is not None and match.name == "llama3.1:latest"


def test_find_model_returns_none_when_absent() -> None:
    from app.services.ollama_service import OllamaModel, OllamaServiceStatus

    status = OllamaServiceStatus(
        reachable=True,
        base_url="http://localhost:11434",
        models=[OllamaModel(name="mistral:latest")],
    )
    assert find_model(status, "llama3.1:latest") is None


def test_langchain_ollama_integration_is_importable() -> None:
    provider, chat_cls = resolve_chat_ollama_class()
    assert provider in {"langchain-ollama", "langchain-community"}
    assert chat_cls is not None


# ---------------------------------------------------------------------------
# Integration tests - require local infrastructure
# ---------------------------------------------------------------------------

database_status = check_database()
ollama_status = check_ollama_service()


@pytest.mark.skipif(not database_status.connected, reason="PostgreSQL not reachable")
def test_postgresql_connectivity() -> None:
    assert database_status.database_name == settings.postgres_db
    assert database_status.current_user == settings.postgres_user


@pytest.mark.skipif(not database_status.connected, reason="PostgreSQL not reachable")
def test_expected_database_extensions_present() -> None:
    assert not database_status.missing_expected_extensions, (
        f"missing extensions: {database_status.missing_expected_extensions}"
    )


@pytest.mark.skipif(not ollama_status.reachable, reason="Ollama not reachable")
def test_configured_model_is_pulled() -> None:
    model = find_model(ollama_status, settings.ollama_model)
    assert model is not None, (
        f"{settings.ollama_model!r} not pulled; available: {ollama_status.model_names}"
    )


@pytest.mark.skipif(
    not ollama_status.reachable or find_model(ollama_status, settings.ollama_model) is None,
    reason="Ollama or configured model unavailable",
)
def test_live_inference_returns_real_output() -> None:
    response = run_llm_smoke_test(
        "Reply with the single word: READY",
        settings=None,
    )
    assert response.text.strip(), "model returned an empty response"
    assert response.latency_ms > 0
    assert response.provider in {"langchain-ollama", "langchain-community"}


def test_unavailable_service_raises_typed_error() -> None:
    """A dead endpoint must raise OllamaUnavailableError, never fake content."""
    from app.core.config import Settings

    broken = Settings(ollama_base_url="http://127.0.0.1:9", ollama_model="does-not-exist:1b")
    with pytest.raises(OllamaUnavailableError):
        run_llm_smoke_test("hello", settings=broken)
