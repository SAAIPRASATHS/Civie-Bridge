"""Standalone Ollama connectivity check.

Phase 1 acceptance script. Run from the repository root:

    python agents/ollama_test.py

It reads OLLAMA_BASE_URL / OLLAMA_MODEL from the environment (or the repo
``.env``), verifies the model is pulled, then sends one real grievance
classification prompt and prints the model's live response.

Exit codes: 0 = success, 1 = service/model/response failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import settings
from app.core.logging import configure_logging
from app.services.ollama_service import (
    OllamaUnavailableError,
    check_ollama_service,
    find_model,
    run_llm_smoke_test,
)

PROMPT = (
    "You are CivicBridge AI, a government grievance understanding assistant.\n\n"
    "Analyze this complaint:\n\n"
    '"Garbage has not been collected near our street for the past five days."\n\n'
    "Return exactly these five labelled lines and nothing else:\n"
    "1. Complaint category\n"
    "2. Responsible department\n"
    "3. Priority\n"
    "4. Location information required\n"
    "5. Recommended next action"
)


def main() -> int:
    configure_logging()

    print("=" * 78)
    print(" CivicBridge AI - Ollama integration check")
    print(f" base_url = {settings.ollama_base_url}")
    print(f" model    = {settings.ollama_model}")
    print("=" * 78)

    status = check_ollama_service()
    if not status.reachable:
        print(f"\n[FAIL] Ollama is not reachable: {status.error}")
        print("       Start it with: ollama serve")
        return 1
    print(f"\n[OK]   Ollama v{status.version} reachable.")
    print(f"[OK]   Models pulled: {', '.join(status.model_names) or 'none'}")

    model = find_model(status, settings.ollama_model)
    if model is None:
        print(f"\n[FAIL] Model {settings.ollama_model!r} is not pulled.")
        print(f"       Run: ollama pull {settings.ollama_model}")
        return 1
    print(
        f"[OK]   Model verified: {model.name} "
        f"({model.parameter_size} {model.quantization_level}, {model.size_human})"
    )

    print("\n--- prompt ------------------------------------------------------------")
    print(PROMPT)
    print("--- model response ----------------------------------------------------")

    try:
        response = run_llm_smoke_test(PROMPT)
    except OllamaUnavailableError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(response.text)
    print("-----------------------------------------------------------------------")
    print(
        f"[OK]   Live inference succeeded in {response.latency_ms:.0f} ms "
        f"via {response.provider} ({response.model})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
