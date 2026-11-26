"""Phase 4 API integration tests: duplicate detection, evidence and human review.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase4_api_integration.py -v

Two tiers, each independently skippable:

* **Deterministic endpoint tests** - the model is replaced by a ``use_llm=False``
  pipeline, so they always run. Duplicate detection is exercised through inline
  candidates and evidence analysis through a generated image, so neither needs
  PostgreSQL.
* **Database-backed tests** - need PostgreSQL and the seeded data; skipped when
  either is unreachable. They cover the stored-complaint scan, the officer
  approve/reject workflow, the audit trail and the guarantee that nothing is
  merged automatically.

The suite also asserts the two structural safety properties: duplicate detection
only ever returns *potential* matches, and evidence analysis never validates or
rejects a complaint.
"""

from __future__ import annotations

import base64
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.duplicate import ComplaintSnapshot
from app.agents.evidence import EvidenceImage
from app.agents.pipeline import ComplaintPipeline
from app.api import duplicates as duplicates_api
from app.api import evidence as evidence_api
from app.database.session import check_database, get_engine, get_session_factory
from app.main import app
from app.models.agent_execution_log import AgentExecutionLog
from app.models.complaint import Complaint
from app.models.complaint_evidence import ComplaintEvidence
from app.models.enums import (
    AgentExecutionStatus,
    AgentName,
    ComplaintCategory,
    ComplaintStatus,
    EvidenceVerificationStatus,
)
from app.models.status_history import ComplaintStatusHistory
from app.schemas.agent import DuplicateDetectionResult, EvidenceAnalysisResult

GARBAGE = "Garbage has not been collected on our street for five days."
OTHER_PROBLEM = "The traffic signal at the main junction has stopped working."
ANNA_NAGAR = (13.0850, 80.2100)


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


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(as_officer, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose agent pipeline is deterministic (no model calls).

    Every Phase 4 endpoint requires an officer or administrator, so the client
    is served as an officer; the role guard still runs on the server.
    """
    pipeline = ComplaintPipeline.build(use_llm=False)
    monkeypatch.setattr(duplicates_api, "get_pipeline", lambda: pipeline)
    monkeypatch.setattr(evidence_api, "get_pipeline", lambda: pipeline)
    return TestClient(app)


def _candidate(
    text: str,
    *,
    category: ComplaintCategory | None = None,
    coordinates: tuple[float, float] | None = None,
    reference_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"text": text}
    if category is not None:
        payload["category"] = category.value
    if coordinates is not None:
        payload["latitude"], payload["longitude"] = coordinates
    if reference_id is not None:
        payload["reference_id"] = reference_id
    return payload


def _png_base64(size: int = 320) -> str:
    """A sharp, high-contrast checkerboard, encoded as base64 PNG."""
    checker = (np.indices((size, size)).sum(axis=0) // 8) % 2
    grey = np.where(checker == 1, 235, 20).astype(np.uint8)
    image = np.repeat(grey[:, :, None], 3, axis=2)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _persist(text: str) -> tuple[uuid.UUID, str]:
    """Create one committed complaint; return its id and reference id."""
    session = get_session_factory()()
    try:
        _, complaint = ComplaintPipeline.build(use_llm=False).analyze_and_persist(text, session)
        session.commit()
        return complaint.id, complaint.reference_id
    finally:
        session.close()


def _purge(*complaint_ids: uuid.UUID) -> None:
    """Remove test complaints and every row that references them."""
    session = get_session_factory()()
    try:
        ids = list(complaint_ids)
        for model in (ComplaintStatusHistory, ComplaintEvidence, AgentExecutionLog):
            session.execute(delete(model).where(model.complaint_id.in_(ids)))
        session.execute(update(Complaint).where(Complaint.id.in_(ids)).values(duplicate_of_id=None))
        session.execute(delete(Complaint).where(Complaint.id.in_(ids)))
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. Duplicate detection API (offline, inline candidates)
# ---------------------------------------------------------------------------


def test_analyze_endpoint_returns_potential_matches(client: TestClient) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={
            "text": GARBAGE,
            "category": "waste_management",
            "latitude": ANNA_NAGAR[0],
            "longitude": ANNA_NAGAR[1],
            "candidates": [
                _candidate(
                    GARBAGE,
                    category=ComplaintCategory.WASTE_MANAGEMENT,
                    coordinates=ANNA_NAGAR,
                    reference_id="CB-twin",
                )
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert DuplicateDetectionResult.model_validate(body)
    assert body["likely_duplicate_found"] is True
    assert body["matches"][0]["is_likely_duplicate"] is True
    assert body["matches"][0]["reference_id"] == "CB-twin"
    assert body["weights"]["text"] == 0.7
    # It is a recommendation, never a declaration or a merge.
    assert body["linking_requires_human_approval"] is True


def test_analyze_endpoint_reports_no_match(client: TestClient) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={
            "text": GARBAGE,
            "category": "waste_management",
            "candidates": [
                _candidate(
                    OTHER_PROBLEM,
                    category=ComplaintCategory.STREET_LIGHTING,
                    coordinates=ANNA_NAGAR,
                    reference_id="CB-signal",
                )
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["likely_duplicate_found"] is False
    assert body["matches"][0]["is_likely_duplicate"] is False
    assert body["matches"][0]["reasons"]
    assert "no pair reached the duplicate threshold" in body["reasoning"]


def test_analyze_endpoint_ranks_multiple_matches(client: TestClient) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={
            "text": GARBAGE,
            "category": "waste_management",
            "latitude": ANNA_NAGAR[0],
            "longitude": ANNA_NAGAR[1],
            "candidates": [
                _candidate(
                    GARBAGE,
                    category=ComplaintCategory.WASTE_MANAGEMENT,
                    coordinates=ANNA_NAGAR,
                    reference_id="CB-twin-a",
                ),
                _candidate(
                    GARBAGE,
                    category=ComplaintCategory.WASTE_MANAGEMENT,
                    coordinates=ANNA_NAGAR,
                    reference_id="CB-twin-b",
                ),
                _candidate(
                    OTHER_PROBLEM,
                    category=ComplaintCategory.STREET_LIGHTING,
                    coordinates=ANNA_NAGAR,
                    reference_id="CB-signal",
                ),
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["candidates_considered"] == 3
    likely = [match for match in body["matches"] if match["is_likely_duplicate"]]
    assert len(likely) == 2
    assert body["best_match"]["reference_id"] in {"CB-twin-a", "CB-twin-b"}
    scores = [match["match_score"] for match in body["matches"]]
    assert scores == sorted(scores, reverse=True)


def test_analyze_endpoint_compares_on_text_when_coordinates_are_missing(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={
            "text": GARBAGE,
            "category": "waste_management",
            "candidates": [
                _candidate(
                    GARBAGE,
                    category=ComplaintCategory.WASTE_MANAGEMENT,
                    reference_id="CB-twin",
                )
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    match = body["matches"][0]
    assert match["geographic_distance_m"] is None
    assert match["geographic_score"] is None
    # Text alone can still flag a likely duplicate; it is never dropped for
    # lack of coordinates.
    assert match["is_likely_duplicate"] is True


def test_analyze_endpoint_requires_a_comparison_set(client: TestClient) -> None:
    response = client.post("/api/duplicates/analyze", json={"text": GARBAGE})

    assert response.status_code == 422


def test_analyze_endpoint_rejects_half_a_coordinate_pair(client: TestClient) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={"text": GARBAGE, "latitude": ANNA_NAGAR[0], "complaint_id": str(uuid.uuid4())},
    )

    assert response.status_code == 422


def test_analyze_endpoint_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={"text": GARBAGE, "candidates": [], "merge_now": True},
    )

    assert response.status_code == 422


def test_list_duplicate_matches_rejects_a_non_uuid_id(client: TestClient) -> None:
    response = client.get("/api/complaints/not-a-uuid/duplicates")

    assert response.status_code == 422


def test_review_rejects_self_linking(client: TestClient) -> None:
    complaint_id = uuid.uuid4()
    response = client.post(
        f"/api/complaints/{complaint_id}/duplicates/review",
        json={"candidate_id": str(complaint_id), "decision": "approve"},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 2. Duplicate detection API (database-backed)
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
def test_list_duplicate_matches_returns_stored_candidates(client: TestClient) -> None:
    subject_id, _ = _persist(GARBAGE)
    twin_id, twin_reference = _persist(GARBAGE)
    try:
        response = client.get(f"/api/complaints/{subject_id}/duplicates")

        assert response.status_code == 200
        body = response.json()
        assert body["likely_duplicate_found"] is True
        assert body["total_matches"] == body["candidates_considered"]
        assert body["returned"] <= body["limit"]
        assert body["items"][0]["reference_id"] == twin_reference
        assert body["items"][0]["is_likely_duplicate"] is True
    finally:
        _purge(subject_id, twin_id)


@requires_database
@requires_seeded_data
def test_list_duplicate_matches_paginates(client: TestClient) -> None:
    subject_id, _ = _persist(GARBAGE)
    twin_id, _ = _persist(GARBAGE)
    try:
        response = client.get(f"/api/complaints/{subject_id}/duplicates?limit=1&offset=0")

        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 1
        assert body["returned"] == 1
        assert len(body["items"]) == 1
    finally:
        _purge(subject_id, twin_id)


@requires_database
def test_list_duplicate_matches_unknown_complaint_is_404(client: TestClient) -> None:
    response = client.get(f"/api/complaints/{uuid.uuid4()}/duplicates")

    assert response.status_code == 404


@requires_database
@requires_seeded_data
def test_analysis_does_not_merge_anything(client: TestClient) -> None:
    subject_id, _ = _persist(GARBAGE)
    twin_id, _ = _persist(GARBAGE)
    try:
        response = client.post(
            "/api/duplicates/analyze",
            json={"text": GARBAGE, "category": "waste_management", "complaint_id": str(subject_id)},
        )
        assert response.status_code == 200
        assert response.json()["likely_duplicate_found"] is True

        session = get_session_factory()()
        try:
            subject = session.get(Complaint, subject_id)
            twin = session.get(Complaint, twin_id)
            # Detection never writes a link; only an officer decision may.
            assert subject.duplicate_of_id is None
            assert twin.duplicate_of_id is None
            assert subject.duplicate_similarity is None
        finally:
            session.close()
    finally:
        _purge(subject_id, twin_id)


@requires_database
def test_analyze_endpoint_unknown_complaint_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/duplicates/analyze",
        json={"text": GARBAGE, "complaint_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 3. Human approval workflow and audit trail
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
def test_review_approve_links_and_records_audit(client: TestClient, officer_user) -> None:
    subject_id, _ = _persist(GARBAGE)
    candidate_id, candidate_reference = _persist(GARBAGE)
    try:
        response = client.post(
            f"/api/complaints/{subject_id}/duplicates/review",
            json={
                "candidate_id": str(candidate_id),
                "decision": "approve",
                "reviewer_id": str(officer_user.id),
                "reason": "same bin, same street",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["linked"] is True
        assert body["audit_recorded"] is True
        assert body["candidate_reference_id"] == candidate_reference
        assert body["status"] == ComplaintStatus.DUPLICATE.value

        session = get_session_factory()()
        try:
            stored = session.get(Complaint, subject_id)
            assert stored.duplicate_of_id == candidate_id
            assert stored.status is ComplaintStatus.DUPLICATE
            assert stored.duplicate_similarity is not None

            history = (
                session.execute(
                    select(ComplaintStatusHistory).where(
                        ComplaintStatusHistory.complaint_id == subject_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
            assert history[0].to_status is ComplaintStatus.DUPLICATE
            assert history[0].changed_by_user_id == officer_user.id
            assert history[0].reason == "same bin, same street"
        finally:
            session.close()
    finally:
        _purge(subject_id, candidate_id)


@requires_database
@requires_seeded_data
def test_review_reject_leaves_the_complaints_separate(client: TestClient, officer_user) -> None:
    subject_id, _ = _persist(GARBAGE)
    candidate_id, _ = _persist(GARBAGE)
    try:
        response = client.post(
            f"/api/complaints/{subject_id}/duplicates/review",
            json={
                "candidate_id": str(candidate_id),
                "decision": "reject",
                "reviewer_id": str(officer_user.id),
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["linked"] is False

        session = get_session_factory()()
        try:
            stored = session.get(Complaint, subject_id)
            assert stored.duplicate_of_id is None
            assert stored.duplicate_similarity is None
            assert stored.status is not ComplaintStatus.DUPLICATE

            history = (
                session.execute(
                    select(ComplaintStatusHistory).where(
                        ComplaintStatusHistory.complaint_id == subject_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
            # A rejection is a no-op transition, recorded so the trail is complete.
            assert history[0].from_status is history[0].to_status
            assert "rejected" in (history[0].reason or "")
        finally:
            session.close()
    finally:
        _purge(subject_id, candidate_id)


@requires_database
@requires_seeded_data
def test_review_unknown_candidate_is_404(client: TestClient) -> None:
    subject_id, _ = _persist(GARBAGE)
    try:
        response = client.post(
            f"/api/complaints/{subject_id}/duplicates/review",
            json={"candidate_id": str(uuid.uuid4()), "decision": "approve"},
        )

        assert response.status_code == 404
    finally:
        _purge(subject_id)


@requires_database
def test_review_unknown_complaint_is_404(client: TestClient) -> None:
    response = client.post(
        f"/api/complaints/{uuid.uuid4()}/duplicates/review",
        json={"candidate_id": str(uuid.uuid4()), "decision": "approve"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 4. Evidence analysis API
# ---------------------------------------------------------------------------


def test_evidence_analyze_endpoint(client: TestClient) -> None:
    response = client.post(
        "/api/evidence/analyze",
        json={
            "file_name": "street.png",
            "content_base64": _png_base64(),
            "content_type": "image/png",
            "category": "waste_management",
        },
    )

    assert response.status_code == 200
    body = response.json()
    analysis = body["analysis"]
    assert EvidenceAnalysisResult.model_validate(analysis)
    assert analysis["image_analyzed"] is True
    assert analysis["image_usable"] is True
    assert analysis["content_digest"]
    # An image is never proof, and the verdict never exceeds "uncertain".
    assert analysis["is_proof_of_validity"] is False
    assert analysis["verification_status"] == EvidenceVerificationStatus.UNCERTAIN.value
    assert analysis["requires_manual_review"] is True
    assert body["persisted"] is False
    assert body["original_evidence_preserved"] is True


def test_evidence_analyze_rejects_a_non_image_with_a_clean_422(client: TestClient) -> None:
    response = client.post(
        "/api/evidence/analyze",
        json={
            "file_name": "notes.txt",
            "content_base64": base64.b64encode(b"this is not an image").decode("ascii"),
        },
    )

    # Phase 7 validates the upload at the boundary: a file that is not a
    # recognised, decodable image is refused with a caller-safe 422 rather than
    # being handed to the agent as a degraded analysis.
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "notes.txt" in detail
    assert "image" in detail


def test_evidence_analyze_rejects_invalid_base64(client: TestClient) -> None:
    response = client.post(
        "/api/evidence/analyze",
        json={"file_name": "broken.png", "content_base64": "not valid base64 *!"},
    )

    assert response.status_code == 422


def test_evidence_analyze_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/evidence/analyze",
        json={
            "file_name": "street.png",
            "content_base64": _png_base64(),
            "auto_verify": True,
        },
    )

    assert response.status_code == 422


def test_evidence_attaching_to_a_complaint_requires_a_storage_reference(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/evidence/analyze",
        json={
            "file_name": "street.png",
            "content_base64": _png_base64(),
            "complaint_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 422


@requires_database
@requires_seeded_data
def test_evidence_analysis_is_stored_against_a_complaint(client: TestClient) -> None:
    complaint_id, _ = _persist(GARBAGE)
    try:
        response = client.post(
            "/api/evidence/analyze",
            json={
                "file_name": "street.png",
                "content_base64": _png_base64(),
                "content_type": "image/png",
                "category": "waste_management",
                "complaint_id": str(complaint_id),
                "storage_reference": "evidence/street.png",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is True
        assert body["evidence_id"] is not None

        session = get_session_factory()()
        try:
            stored = session.get(ComplaintEvidence, uuid.UUID(body["evidence_id"]))
            assert stored is not None
            assert stored.complaint_id == complaint_id
            assert stored.storage_path == "evidence/street.png"
            assert stored.file_size_bytes is not None
            assert stored.requires_manual_review is True
            assert stored.verification_status is not EvidenceVerificationStatus.VERIFIED_RELEVANT

            # The complaint's own status is untouched: evidence never decides it.
            complaint = session.get(Complaint, complaint_id)
            assert complaint.status is not ComplaintStatus.REJECTED
        finally:
            session.close()
    finally:
        _purge(complaint_id)


# ---------------------------------------------------------------------------
# 5. Pipeline persistence of the duplicate and evidence steps
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
def test_pipeline_persists_duplicate_and_evidence_logs() -> None:
    pipeline = ComplaintPipeline.build(use_llm=False)
    session = get_session_factory()()
    try:
        candidate = ComplaintSnapshot(
            text=GARBAGE,
            reference_id="CB-twin",
            category=ComplaintCategory.WASTE_MANAGEMENT,
            latitude=ANNA_NAGAR[0],
            longitude=ANNA_NAGAR[1],
        )
        image = EvidenceImage.from_bytes("street.png", base64.b64decode(_png_base64()))

        analysis, complaint = pipeline.analyze_and_persist(
            GARBAGE,
            session,
            duplicate_candidates=[candidate],
            images=[image],
            latitude=ANNA_NAGAR[0],
            longitude=ANNA_NAGAR[1],
        )

        assert analysis.duplicate is not None
        assert analysis.duplicate.likely_duplicate_found is True
        assert len(analysis.evidence) == 1

        logs = (
            session.execute(
                select(AgentExecutionLog)
                .where(AgentExecutionLog.complaint_id == complaint.id)
                .order_by(AgentExecutionLog.step_index)
            )
            .scalars()
            .all()
        )
        assert [log.agent_name for log in logs] == [
            AgentName.INTAKE,
            AgentName.CLASSIFICATION,
            AgentName.DUPLICATE_DETECTION,
            AgentName.EVIDENCE_VERIFICATION,
        ]
        assert [log.step_index for log in logs] == [0, 1, 2, 3]
        # Both new steps stop at a human decision.
        assert logs[2].status is AgentExecutionStatus.AWAITING_HUMAN
        assert logs[3].status is AgentExecutionStatus.AWAITING_HUMAN
        assert logs[3].output_payload["is_proof_of_validity"] is False
    finally:
        # Never committed: the seeded dataset is left untouched.
        session.rollback()
        session.close()
