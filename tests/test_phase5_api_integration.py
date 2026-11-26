"""Phase 5 API integration tests: routing, resolution and the assignment workflow.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase5_api_integration.py -v

Two tiers, each independently skippable:

* **Deterministic endpoint tests** - routing and resolution are pure functions of
  the prototype reference data, so the inline endpoints always run without
  PostgreSQL.
* **Database-backed tests** - need PostgreSQL and the seeded data; skipped
  otherwise. They cover the stored-complaint recommendation, the officer
  approve/reject workflow, the audit trail and the guarantee that nothing is
  assigned, rejected, resolved or closed automatically.

The suite also asserts the phase's two safety properties: the agents only ever
recommend (no recommendation endpoint writes anything), and only an officer or
administrator decision assigns a complaint.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.persistence import generate_reference_id
from app.database.session import check_database, get_engine, get_session_factory
from app.main import app
from app.models.agent_execution_log import AgentExecutionLog
from app.models.assignment import ComplaintAssignment
from app.models.complaint import Complaint
from app.models.complaint_evidence import ComplaintEvidence
from app.models.enums import AssignmentApprovalStatus, ComplaintCategory, ComplaintStatus
from app.models.status_history import ComplaintStatusHistory
from app.schemas.agent import ResolutionRecommendation, RoutingRecommendation

GARBAGE = "Garbage has not been collected on our street for five days."


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


def _user_id(role: str) -> uuid.UUID | None:
    if not _database_status.connected:
        return None
    try:
        with get_engine().connect() as connection:
            return connection.execute(
                text("SELECT id FROM users WHERE role = :role LIMIT 1"), {"role": role}
            ).scalar_one_or_none()
    except Exception:
        return None


_SEEDED_OFFICER = _user_id("officer")
_SEEDED_CITIZEN = _user_id("citizen")

requires_officer = pytest.mark.skipif(
    _SEEDED_OFFICER is None,
    reason="no officer stored; the assignment approval workflow needs one",
)
requires_citizen = pytest.mark.skipif(
    _SEEDED_CITIZEN is None,
    reason="no citizen stored; the unauthorized-decision check needs one",
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(as_officer) -> TestClient:
    """The routing and resolution agents are deterministic; no model is needed.

    The stored-complaint and decision endpoints require an officer, so the
    client is served as one; the server still performs the role check.
    """
    return TestClient(app)


@pytest.fixture()
def citizen_client(as_citizen) -> TestClient:
    """A client served as a signed-in citizen, to prove the officer guard refuses."""
    return TestClient(app)


def _persist_complaint(
    *,
    text_value: str = GARBAGE,
    category: ComplaintCategory | None = ComplaintCategory.WASTE_MANAGEMENT,
    ward: str | None = "5",
    status: ComplaintStatus = ComplaintStatus.SUBMITTED,
) -> uuid.UUID:
    """Create one committed complaint with full control over its routing inputs."""
    session = get_session_factory()()
    try:
        complaint = Complaint(
            reference_id=generate_reference_id(session),
            description=text_value,
            category=category,
            ward=ward,
            status=status,
            is_synthetic=True,
        )
        session.add(complaint)
        session.commit()
        return complaint.id
    finally:
        session.close()


def _purge(*complaint_ids: uuid.UUID) -> None:
    """Remove test complaints and every row that references them."""
    session = get_session_factory()()
    try:
        ids = list(complaint_ids)
        for model in (
            ComplaintAssignment,
            ComplaintStatusHistory,
            ComplaintEvidence,
            AgentExecutionLog,
        ):
            session.execute(delete(model).where(model.complaint_id.in_(ids)))
        session.execute(update(Complaint).where(Complaint.id.in_(ids)).values(duplicate_of_id=None))
        session.execute(delete(Complaint).where(Complaint.id.in_(ids)))
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. Routing recommendations (offline)
# ---------------------------------------------------------------------------


def test_routing_recommend_known_category(client: TestClient) -> None:
    response = client.post(
        "/api/routing/recommend",
        json={"category": "waste_management", "ward": "5"},
    )

    assert response.status_code == 200
    body = response.json()
    assert RoutingRecommendation.model_validate(body)
    assert body["recommended_department_code"] == "SWM"
    assert body["recommended_department_name"]
    assert body["is_prototype_data"] is True
    assert body["matched_rules"]
    assert body["requires_human_review"] is False


def test_routing_recommend_unknown_category_is_not_guessed(client: TestClient) -> None:
    response = client.post("/api/routing/recommend", json={"text": GARBAGE})

    assert response.status_code == 200
    body = response.json()
    assert body["recommended_department_code"] is None
    assert "category" in body["missing_information"]
    assert body["requires_human_review"] is True
    assert body["confidence"] == 0.0


def test_routing_recommend_missing_location_is_flagged(client: TestClient) -> None:
    response = client.post("/api/routing/recommend", json={"category": "road_maintenance"})

    assert response.status_code == 200
    body = response.json()
    assert body["recommended_department_code"] == "RIM"
    assert "ward_or_location" in body["missing_information"]
    assert body["requires_human_review"] is True


def test_routing_recommend_ambiguous_location_is_flagged(client: TestClient) -> None:
    response = client.post(
        "/api/routing/recommend",
        json={
            "category": "water_supply",
            "location_text": "leaking pipeline between ward 3 and ward 7",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requires_human_review"] is True
    assert "more than one ward" in (body["review_reason"] or "")


def test_routing_recommend_rejects_half_a_coordinate_pair(client: TestClient) -> None:
    response = client.post(
        "/api/routing/recommend",
        json={"category": "water_supply", "latitude": 13.08},
    )

    assert response.status_code == 422


def test_routing_recommend_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/routing/recommend",
        json={"category": "water_supply", "assign_now": True},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 2. Resolution recommendations (offline)
# ---------------------------------------------------------------------------


def test_resolution_recommend_discloses_an_unverified_sla(client: TestClient) -> None:
    response = client.post("/api/resolution/recommend", json={"category": "water_supply"})

    assert response.status_code == 200
    body = response.json()
    assert ResolutionRecommendation.model_validate(body)
    assert body["guideline_code"] == "GDL-WSS-001"
    assert body["sla_hours"] == 24
    assert body["sla_is_verified"] is False
    assert body["sla_disclaimer"]
    # Recommendation only: nothing is completed and no status change is applied.
    assert body["recommendation_only"] is True
    assert body["no_auto_resolution"] is True
    assert body["requires_human_approval"] is True
    assert body["status_change_applied"] is False
    assert body["completed_actions"] == []


def test_resolution_recommend_explains_a_missing_guideline(client: TestClient) -> None:
    response = client.post("/api/resolution/recommend", json={"text": GARBAGE})

    assert response.status_code == 200
    body = response.json()
    assert body["guideline_code"] is None
    assert body["sla_hours"] is None
    assert body["requires_human_review"] is True
    assert "no applicable prototype service guideline" in body["sla_note"].lower()
    assert body["no_auto_resolution"] is True
    assert body["status_change_applied"] is False


def test_resolution_recommend_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/api/resolution/recommend",
        json={"category": "water_supply", "resolve_now": True},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 3. Stored-complaint routing recommendation (database)
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
def test_get_routing_for_a_stored_complaint(client: TestClient) -> None:
    complaint_id = _persist_complaint(category=ComplaintCategory.STREET_LIGHTING, ward="9")
    try:
        response = client.get(f"/api/complaints/{complaint_id}/routing")

        assert response.status_code == 200
        body = response.json()
        assert body["recommended_department_code"] == "SLE"
        assert body["category"] == "street_lighting"
    finally:
        _purge(complaint_id)


@requires_database
def test_get_routing_for_an_unknown_complaint_is_404(client: TestClient) -> None:
    response = client.get(f"/api/complaints/{uuid.uuid4()}/routing")

    assert response.status_code == 404


@requires_database
def test_get_routing_rejects_a_non_uuid_id(client: TestClient) -> None:
    response = client.get("/api/complaints/not-a-uuid/routing")

    assert response.status_code == 422


@requires_database
@requires_seeded_data
def test_recommendation_endpoints_do_not_assign_anything(client: TestClient) -> None:
    complaint_id = _persist_complaint()
    try:
        assert client.get(f"/api/complaints/{complaint_id}/routing").status_code == 200
        assert (
            client.post(
                "/api/resolution/recommend", json={"category": "waste_management"}
            ).status_code
            == 200
        )

        session = get_session_factory()()
        try:
            complaint = session.get(Complaint, complaint_id)
            # A recommendation never writes an assignment or changes the status.
            assert complaint.current_department_id is None
            assert complaint.status is ComplaintStatus.SUBMITTED
            assignments = (
                session.execute(
                    select(ComplaintAssignment).where(
                        ComplaintAssignment.complaint_id == complaint_id
                    )
                )
                .scalars()
                .all()
            )
            assert assignments == []
        finally:
            session.close()
    finally:
        _purge(complaint_id)


# ---------------------------------------------------------------------------
# 4. Officer assignment workflow and audit trail (database)
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_approve_assigns_and_records_audit(client: TestClient, officer_user) -> None:
    complaint_id = _persist_complaint()
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={
                "decision": "approve",
                "reviewer_id": str(officer_user.id),
                "reason": "correct department for a waste complaint",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["assigned"] is True
        assert body["approval_status"] == AssignmentApprovalStatus.APPROVED.value
        assert body["department_code"] == "SWM"
        assert body["status"] == ComplaintStatus.ASSIGNED.value
        assert body["audit_recorded"] is True

        session = get_session_factory()()
        try:
            complaint = session.get(Complaint, complaint_id)
            assert complaint.status is ComplaintStatus.ASSIGNED
            assert complaint.current_department_id is not None
            # An assignment is not a resolution or a closure.
            assert complaint.resolved_at is None
            assert complaint.closed_at is None

            assignment = session.get(ComplaintAssignment, uuid.UUID(body["assignment_id"]))
            assert assignment.approval_status is AssignmentApprovalStatus.APPROVED
            assert assignment.approved_by_user_id == officer_user.id
            assert assignment.approved_at is not None
            assert assignment.ai_explanation
            assert assignment.ai_confidence is not None

            history = (
                session.execute(
                    select(ComplaintStatusHistory).where(
                        ComplaintStatusHistory.complaint_id == complaint_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
            assert history[0].from_status is ComplaintStatus.SUBMITTED
            assert history[0].to_status is ComplaintStatus.ASSIGNED
            assert history[0].changed_by_user_id == officer_user.id
        finally:
            session.close()
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_reject_leaves_the_complaint_unchanged(client: TestClient, officer_user) -> None:
    complaint_id = _persist_complaint()
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={
                "decision": "reject",
                "reviewer_id": str(officer_user.id),
                "reason": "wrong ward, needs manual triage",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["assigned"] is False
        assert body["approval_status"] == AssignmentApprovalStatus.REJECTED.value
        assert body["status"] == ComplaintStatus.SUBMITTED.value

        session = get_session_factory()()
        try:
            complaint = session.get(Complaint, complaint_id)
            assert complaint.current_department_id is None
            assert complaint.status is ComplaintStatus.SUBMITTED

            assignment = session.get(ComplaintAssignment, uuid.UUID(body["assignment_id"]))
            assert assignment.approval_status is AssignmentApprovalStatus.REJECTED
            assert assignment.rejection_reason == "wrong ward, needs manual triage"
            assert assignment.approved_at is None

            history = (
                session.execute(
                    select(ComplaintStatusHistory).where(
                        ComplaintStatusHistory.complaint_id == complaint_id
                    )
                )
                .scalars()
                .all()
            )
            # A rejection is a no-op transition, recorded so the trail is complete.
            assert len(history) == 1
            assert history[0].from_status is history[0].to_status
            assert "rejected" in (history[0].reason or "")
        finally:
            session.close()
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_is_not_resolved_or_closed(client: TestClient, officer_user) -> None:
    complaint_id = _persist_complaint()
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "approve", "reviewer_id": str(officer_user.id)},
        )

        assert response.status_code == 200
        assert response.json()["status"] == ComplaintStatus.ASSIGNED.value
        assert response.json()["status"] not in {
            ComplaintStatus.RESOLVED.value,
            ComplaintStatus.CLOSED.value,
            ComplaintStatus.REJECTED.value,
        }
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_cannot_assign_a_resolved_complaint(client: TestClient, officer_user) -> None:
    complaint_id = _persist_complaint(status=ComplaintStatus.RESOLVED)
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "approve", "reviewer_id": str(officer_user.id)},
        )

        assert response.status_code == 409

        session = get_session_factory()()
        try:
            complaint = session.get(Complaint, complaint_id)
            assert complaint.status is ComplaintStatus.RESOLVED
            assert complaint.current_department_id is None
        finally:
            session.close()
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_without_a_department_recommendation_is_409(
    client: TestClient, officer_user
) -> None:
    complaint_id = _persist_complaint(category=None)
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "approve", "reviewer_id": str(officer_user.id)},
        )

        assert response.status_code == 409
    finally:
        _purge(complaint_id)


# ---------------------------------------------------------------------------
# 5. Unauthorized and invalid decisions
# ---------------------------------------------------------------------------


@requires_database
@requires_seeded_data
@requires_citizen
def test_decision_by_a_citizen_is_forbidden(citizen_client: TestClient) -> None:
    complaint_id = _persist_complaint()
    try:
        response = citizen_client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "approve"},
        )

        assert response.status_code == 403

        session = get_session_factory()()
        try:
            complaint = session.get(Complaint, complaint_id)
            assert complaint.current_department_id is None
            assert complaint.status is ComplaintStatus.SUBMITTED
        finally:
            session.close()
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_with_a_mismatched_reviewer_is_forbidden(client: TestClient, officer_user) -> None:
    # The reviewer is taken from the session; a body that names a different
    # account is refused rather than silently overridden.
    complaint_id = _persist_complaint()
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "approve", "reviewer_id": str(uuid.uuid4())},
        )

        assert response.status_code == 403
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
def test_decision_with_an_invalid_decision_is_422(client: TestClient) -> None:
    complaint_id = _persist_complaint()
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "maybe"},
        )

        assert response.status_code == 422
    finally:
        _purge(complaint_id)


@requires_database
@requires_seeded_data
@requires_officer
def test_decision_without_a_reviewer_uses_the_session_account(
    client: TestClient, officer_user
) -> None:
    # ``reviewer_id`` is optional: the server records the authenticated account.
    complaint_id = _persist_complaint()
    try:
        response = client.post(
            f"/api/complaints/{complaint_id}/routing/decision",
            json={"decision": "approve"},
        )

        assert response.status_code == 200
        assert response.json()["assigned"] is True

        session = get_session_factory()()
        try:
            history = (
                session.execute(
                    select(ComplaintStatusHistory).where(
                        ComplaintStatusHistory.complaint_id == complaint_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
            assert history[0].changed_by_user_id == officer_user.id
        finally:
            session.close()
    finally:
        _purge(complaint_id)


def test_decision_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        f"/api/complaints/{uuid.uuid4()}/routing/decision",
        json={"decision": "approve", "auto_close": True},
    )

    assert response.status_code == 422


@requires_database
def test_decision_with_an_unknown_complaint_is_404(client: TestClient) -> None:
    response = client.post(
        f"/api/complaints/{uuid.uuid4()}/routing/decision",
        json={"decision": "approve"},
    )

    assert response.status_code == 404
