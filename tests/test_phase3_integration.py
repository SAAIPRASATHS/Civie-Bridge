"""Phase 3 integration tests: pipeline, persistence, API and live Ollama.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase3_integration.py -v

Three tiers, each independently skippable:

* **Deterministic pipeline and API tests** - the model is replaced by a scripted
  fake, so they always run.
* **Persistence tests** - need PostgreSQL; skipped when it is unreachable.
* **Live tests** - make real inference calls against ``llama3.1:latest``;
  skipped when Ollama or the configured model is missing.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.classification import ClassificationAgent
from app.agents.errors import AgentLlmError
from app.agents.intake import IntakeAgent
from app.agents.llm_client import LlmCallResult
from app.agents.pipeline import ComplaintPipeline, get_pipeline
from app.core.config import settings
from app.database.session import check_database, get_engine, get_session_factory
from app.datasets.reference_data import DEPARTMENT_DIRECTORY
from app.main import app
from app.models.agent_execution_log import AgentExecutionLog
from app.models.complaint import Complaint
from app.models.enums import (
    AgentName,
    ComplaintCategory,
    ComplaintLanguage,
    ComplaintPriority,
)
from app.schemas.agent import (
    ANALYSIS_PROVENANCE,
    ClassificationResult,
    ComplaintAnalysis,
    IntakeResult,
)
from app.services.ollama_service import check_ollama_service, find_model

ENGLISH_COMPLAINT = "For the past five days garbage has not been collected near our street."
WATER_COMPLAINT = "No water supply in Ward 10 for two days."


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------

_database_status = check_database()
requires_database = pytest.mark.skipif(
    not _database_status.connected,
    reason="PostgreSQL not reachable; run `python -m app.cli health` first",
)


def _row_count(table: str) -> int:
    if not _database_status.connected:
        return 0
    try:
        with get_engine().connect() as connection:
            return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
    except Exception:  # table not migrated yet
        return 0


requires_seeded_data = pytest.mark.skipif(
    _row_count("departments") == 0,
    reason="no departments stored; run `python -m app.cli seed-db` first",
)

_ollama_status = check_ollama_service()
_ollama_model = (
    find_model(_ollama_status, settings.ollama_model) if _ollama_status.reachable else None
)
requires_ollama = pytest.mark.skipif(
    _ollama_model is None,
    reason=f"Ollama model {settings.ollama_model!r} unavailable; live inference test skipped",
)


# ---------------------------------------------------------------------------
# Fake LLM and payload helpers
# ---------------------------------------------------------------------------


class ScriptedLlm:
    """Fake ``LlmClient`` returning pre-scripted responses in order."""

    def __init__(self, responses: list[str] | None = None, *, available: bool = True) -> None:
        self.responses = list(responses or [])
        self.available = available
        self.calls: list[dict[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def complete(self, *, system: str, user: str) -> LlmCallResult:
        self.calls.append({"system": system, "user": user})
        if not self.responses:
            raise AgentLlmError("no scripted response left")
        return LlmCallResult(
            text=self.responses.pop(0),
            model="scripted-fake",
            provider="fake",
            latency_ms=1.0,
        )


def _intake_json() -> str:
    return json.dumps(
        {
            "normalized_description": "Garbage has not been collected for five days.",
            "problem_type": "garbage_not_collected",
            "duration_days": 5,
            "duration_text": "five days",
            "location_text": None,
            "landmarks": [],
            "missing_information": ["exact_location"],
            "clarification_questions": ["Which street is affected?"],
            "confidence": 0.8,
            "reasoning": "The citizen reports five days of missed collection.",
        }
    )


def _classification_json() -> str:
    return json.dumps(
        {
            "category": "waste_management",
            "subcategory": "garbage_not_collected",
            "priority": "high",
            "urgency_score": 0.6,
            "confidence": 0.85,
            "reasoning": "Garbage has not been collected for five days.",
            "recommended_next_action": "Dispatch the collection beat to the reported street.",
        }
    )


# ---------------------------------------------------------------------------
# 1. Pipeline (deterministic and with a scripted model)
# ---------------------------------------------------------------------------


def test_pipeline_runs_both_agents_without_a_model() -> None:
    pipeline = ComplaintPipeline.build(use_llm=False)

    analysis = pipeline.analyze(ENGLISH_COMPLAINT)

    assert isinstance(analysis, ComplaintAnalysis)
    assert analysis.steps == ["intake", "classification"]
    assert analysis.llm_calls == 0
    assert analysis.intake.language is ComplaintLanguage.EN
    assert analysis.classification.category is ComplaintCategory.WASTE_MANAGEMENT
    assert analysis.classification.department_code == "SWM"
    assert analysis.persisted is False
    assert analysis.reference_id is None
    assert analysis.provenance == ANALYSIS_PROVENANCE


def test_pipeline_makes_at_most_one_model_call_per_agent() -> None:
    fake = ScriptedLlm([_intake_json(), _classification_json()])
    pipeline = ComplaintPipeline.build(llm=fake, use_llm=True)

    analysis = pipeline.analyze(ENGLISH_COMPLAINT)

    assert len(fake.calls) == 2
    assert analysis.llm_calls == 2
    assert analysis.intake.llm_used is True
    assert analysis.classification.llm_used is True
    assert analysis.classification.needs_human_review is False


def test_pipeline_still_analyses_when_the_model_is_down() -> None:
    fake = ScriptedLlm([], available=False)
    pipeline = ComplaintPipeline.build(llm=fake, use_llm=True)

    analysis = pipeline.analyze(ENGLISH_COMPLAINT)

    assert fake.calls == []
    assert analysis.intake.degraded is True
    assert analysis.classification.degraded is True
    assert analysis.classification.category is ComplaintCategory.WASTE_MANAGEMENT


# ---------------------------------------------------------------------------
# 2. Database persistence
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
def test_analysis_is_persisted_with_one_log_per_agent() -> None:
    pipeline = ComplaintPipeline.build(use_llm=False)
    session = get_session_factory()()
    try:
        analysis, complaint = pipeline.analyze_and_persist(ENGLISH_COMPLAINT, session)

        assert analysis.persisted is True
        assert analysis.reference_id == complaint.reference_id
        assert complaint.reference_id.startswith("CB-")
        assert complaint.description == ENGLISH_COMPLAINT
        assert complaint.normalized_description
        assert complaint.language is ComplaintLanguage.EN
        assert complaint.category is ComplaintCategory.WASTE_MANAGEMENT
        assert complaint.priority is ComplaintPriority.HIGH
        assert complaint.classification_confidence is not None
        assert complaint.current_department_id is not None
        assert analysis.steps == ["intake", "classification", "persisted"]

        logs = (
            session.execute(
                select(AgentExecutionLog)
                .where(AgentExecutionLog.complaint_id == complaint.id)
                .order_by(AgentExecutionLog.step_index)
            )
            .scalars()
            .all()
        )
        assert [log.agent_name for log in logs] == [AgentName.INTAKE, AgentName.CLASSIFICATION]
        assert [log.step_index for log in logs] == [0, 1]
        assert logs[0].input_payload["text"] == ENGLISH_COMPLAINT
    finally:
        # The flush is rolled back: the seeded dataset is never mutated.
        session.rollback()
        session.close()


@requires_database
@requires_seeded_data
def test_undetermined_complaint_is_persisted_for_human_review() -> None:
    pipeline = ComplaintPipeline.build(use_llm=False)
    session = get_session_factory()()
    try:
        _, complaint = pipeline.analyze_and_persist(
            "Something is wrong in our area please fix it soon", session
        )

        assert complaint.category is None
        assert complaint.current_department_id is None
        log = session.execute(
            select(AgentExecutionLog).where(
                AgentExecutionLog.complaint_id == complaint.id,
                AgentExecutionLog.step_index == 1,
            )
        ).scalar_one()
        assert log.status.value == "awaiting_human"
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# 3. API integration (scripted model, so no inference happens)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    pipeline = ComplaintPipeline.build(use_llm=False)
    monkeypatch.setattr("app.api.agents.get_pipeline", lambda: pipeline)
    return TestClient(app)


def test_api_intake_endpoint(client: TestClient) -> None:
    response = client.post("/api/agents/intake", json={"text": ENGLISH_COMPLAINT})

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["duration_days"] == 5.0
    assert IntakeResult.model_validate(body)


def test_api_intake_rejects_blank_text(client: TestClient) -> None:
    response = client.post("/api/agents/intake", json={"text": "   "})

    assert response.status_code == 422


def test_api_intake_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post("/api/agents/intake", json={"text": "garbage", "extra": 1})

    assert response.status_code == 422


def test_api_classify_endpoint_accepts_raw_text(client: TestClient) -> None:
    response = client.post("/api/agents/classify", json={"text": WATER_COMPLAINT})

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "water_supply"
    assert body["department_code"] == "WSS"
    assert ClassificationResult.model_validate(body)


def test_api_classify_accepts_a_previous_intake_result(client: TestClient) -> None:
    intake = client.post("/api/agents/intake", json={"text": WATER_COMPLAINT}).json()

    response = client.post("/api/agents/classify", json={"intake": intake})

    assert response.status_code == 200
    assert response.json()["category"] == "water_supply"


def test_api_classify_requires_one_input(client: TestClient) -> None:
    response = client.post("/api/agents/classify", json={})

    assert response.status_code == 422


def test_api_analyze_complaint_runs_both_agents(client: TestClient) -> None:
    response = client.post("/api/agents/analyze-complaint", json={"text": ENGLISH_COMPLAINT})

    assert response.status_code == 200
    body = response.json()
    assert body["intake"]["language"] == "en"
    assert body["classification"]["category"] == "waste_management"
    assert body["persisted"] is False
    assert body["steps"] == ["intake", "classification"]
    assert ComplaintAnalysis.model_validate(body)


def _delete_complaint(complaint_id: uuid.UUID) -> None:
    """Remove a complaint and its agent logs so the seeded dataset is untouched."""
    session = get_session_factory()()
    try:
        session.execute(
            delete(AgentExecutionLog).where(AgentExecutionLog.complaint_id == complaint_id)
        )
        session.execute(delete(Complaint).where(Complaint.id == complaint_id))
        session.commit()
    finally:
        session.close()


@requires_database
@requires_seeded_data
def test_api_analyze_complaint_persists_when_requested(client: TestClient) -> None:
    response = client.post(
        "/api/agents/analyze-complaint",
        json={"text": ENGLISH_COMPLAINT, "persist": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is True
    assert body["reference_id"].startswith("CB-")

    complaint_id = uuid.UUID(body["complaint_id"])
    session = get_session_factory()()
    try:
        stored = session.get(Complaint, complaint_id)
        assert stored is not None
        assert stored.is_synthetic is True
        assert stored.category is ComplaintCategory.WASTE_MANAGEMENT
        logs = (
            session.execute(
                select(AgentExecutionLog).where(AgentExecutionLog.complaint_id == complaint_id)
            )
            .scalars()
            .all()
        )
        assert len(logs) == 2
    finally:
        session.rollback()
        session.close()
        _delete_complaint(complaint_id)


# ---------------------------------------------------------------------------
# 4. Live inference against the local Ollama model
#
# These tests share the process-wide LLM response cache, so the complaint texts
# are reused deliberately: the third test re-runs the same prompts as the first
# two and therefore costs no extra inference.
# ---------------------------------------------------------------------------


@requires_ollama
def test_live_intake_agent_against_local_model() -> None:
    result = IntakeAgent(use_llm=True).run(ENGLISH_COMPLAINT)

    assert isinstance(result, IntakeResult)
    assert result.original_text == ENGLISH_COMPLAINT
    assert result.language is ComplaintLanguage.EN
    assert result.duration_days == 5.0
    # Either the model answered or the agent degraded deterministically; both
    # are valid, and neither invents a location.
    assert result.llm_used or result.degraded


@requires_ollama
def test_live_classification_agent_against_local_model() -> None:
    intake = IntakeAgent(use_llm=True).run(ENGLISH_COMPLAINT)
    result = ClassificationAgent(use_llm=True).run(intake)

    assert isinstance(result, ClassificationResult)
    assert result.category is not None
    # Whichever category the keywords and the model settle on, the department
    # must be the prototype directory entry for that category.
    expected_codes = {entry["category"]: entry["code"] for entry in DEPARTMENT_DIRECTORY}
    assert result.department_code == expected_codes[result.category]
    assert result.recommended_next_action
    assert result.llm_used or result.degraded


@requires_ollama
def test_live_full_pipeline_end_to_end() -> None:
    analysis = get_pipeline().analyze(ENGLISH_COMPLAINT)

    assert isinstance(analysis, ComplaintAnalysis)
    assert analysis.steps == ["intake", "classification"]
    assert analysis.intake.original_text == ENGLISH_COMPLAINT
    assert analysis.classification.category is not None
    assert analysis.classification.priority in set(ComplaintPriority)
    assert analysis.total_latency_ms > 0
    assert analysis.classification.department_code in {"SWM", "RIM", "WSS", "SLE"}
