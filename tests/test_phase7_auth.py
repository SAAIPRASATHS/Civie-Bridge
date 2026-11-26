"""Phase 7 unit and integration tests: authentication, RBAC and the portals.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase7_auth.py -v

Five tiers, each independently skippable:

* **Security primitives** - the scrypt password hash and the HMAC-signed session
  token, tested with no service and no database.
* **Upload validation** - size, declared-type, magic-byte and decodability
  checks over an evidence image; no service needed.
* **Auth endpoints and RBAC** - the guard behaviour (anonymous -> 401, a citizen
  on an officer route -> 403) runs without a database; the sign-in round trip
  needs PostgreSQL and the seeded accounts.
* **Citizen portal** - submit, own-only visibility and idempotency; database and
  a deterministic workflow.
* **Officer dashboard** - the read-only list, queue, directory and detail views;
  database only.

The tests assert the phase's structural safety properties: the browser is never
trusted to name the caller (the server re-reads the account from the signed
cookie), a citizen can never read another citizen's complaint, and the dashboard
never performs a consequential action.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys
import time
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

from app.agents.pipeline import ComplaintPipeline
from app.agents.workflow import (
    ComplaintWorkflow,
    WorkflowDependencies,
    build_checkpointer,
)
from app.api.citizens import (
    CitizenComplaintDetail,
    CitizenComplaintPage,
    ComplaintSubmissionResponse,
)
from app.api.deps import get_optional_user
from app.api.officers import (
    DepartmentDirectoryPage,
    OfficerComplaintDetail,
    OfficerComplaintPage,
)
from app.core.config import settings
from app.core.security import (
    HASH_PREFIX,
    create_session_token,
    hash_password,
    parse_session_token,
    verify_password,
)
from app.core.uploads import (
    UploadValidationError,
    sniff_mime_type,
    validate_image_upload,
)
from app.database.session import check_database, get_engine, get_session_factory
from app.main import app
from app.models.complaint import Complaint
from app.models.enums import ComplaintLanguage, ComplaintStatus, UserRole
from app.models.status_history import ComplaintStatusHistory
from app.models.user import User

GARBAGE = "Garbage has not been collected on our street for five days."


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------

_database_status = check_database()
requires_database = pytest.mark.skipif(
    not _database_status.connected,
    reason="PostgreSQL not reachable; run `python -m app.cli health` first",
)


def _role_present(role: UserRole) -> bool:
    if not _database_status.connected:
        return False
    try:
        with get_engine().connect() as connection:
            return (
                connection.execute(
                    text("SELECT 1 FROM users WHERE role = :role LIMIT 1"),
                    {"role": role.value},
                ).first()
                is not None
            )
    except Exception:  # table not migrated yet
        return False


_SEEDED_CITIZEN = _role_present(UserRole.CITIZEN)
_SEEDED_OFFICER = _role_present(UserRole.OFFICER)

requires_citizen = pytest.mark.skipif(
    not _SEEDED_CITIZEN,
    reason="no citizen stored; run `python -m app.cli seed-db` first",
)
requires_officer = pytest.mark.skipif(
    not _SEEDED_OFFICER,
    reason="no officer stored; run `python -m app.cli seed-db` first",
)


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


def _workflow() -> ComplaintWorkflow:
    """A deterministic workflow (``use_llm=False``): no model is needed."""
    pipeline = ComplaintPipeline.build(settings=settings, use_llm=False)
    deps = WorkflowDependencies.build(settings=settings, pipeline=pipeline)
    return ComplaintWorkflow(deps=deps, checkpointer=build_checkpointer())


def _png_base64(size: int = 64) -> str:
    checker = (np.indices((size, size)).sum(axis=0) // 8) % 2
    grey = np.where(checker == 1, 235, 20).astype(np.uint8)
    image = np.repeat(grey[:, :, None], 3, axis=2)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _purge(*complaint_ids: uuid.UUID) -> None:
    """Remove test complaints and every row that references them."""
    if not complaint_ids or not _database_status.connected:
        return
    session = get_session_factory()()
    try:
        ids = list(complaint_ids)
        session.execute(
            delete(ComplaintStatusHistory).where(ComplaintStatusHistory.complaint_id.in_(ids))
        )
        session.execute(update(Complaint).where(Complaint.id.in_(ids)).values(duplicate_of_id=None))
        session.execute(delete(Complaint).where(Complaint.id.in_(ids)))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def client() -> TestClient:
    """A plain client; a test sets the acting role with a conftest fixture."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Security primitives (no service, no database)
# ---------------------------------------------------------------------------


def test_password_hash_is_self_describing_and_salted() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first.startswith(HASH_PREFIX)
    assert first.count("$") == 5
    # A fresh salt per call means the same password never yields the same hash.
    assert first != second


def test_verify_password_accepts_and_rejects() -> None:
    stored = hash_password("s3cret-passphrase")

    assert verify_password("s3cret-passphrase", stored) is True
    assert verify_password("wrong-passphrase", stored) is False
    assert verify_password("", stored) is False


def test_verify_password_rejects_placeholder_and_malformed_hashes() -> None:
    # The Phase 2 placeholder predates authentication and can never sign in.
    assert verify_password("anything", "prototype:no-auth-configured") is False
    assert verify_password("anything", "scrypt$not-a-number$8$1$aaa$bbb") is False
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False


def test_hash_password_requires_a_non_empty_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_session_token_round_trips() -> None:
    user_id = uuid.uuid4()
    token = create_session_token(user_id, secret="k", ttl_seconds=3600)

    assert parse_session_token(token, secret="k") == user_id


def test_tampered_token_is_rejected() -> None:
    user_id = uuid.uuid4()
    token = create_session_token(user_id, secret="k", ttl_seconds=3600)
    payload, signature = token.rsplit(".", 1)

    # Re-signing the same payload with an altered user id must not validate.
    forged = f"{uuid.uuid4()}.{payload.split('.')[1]}.{signature}"
    assert parse_session_token(forged, secret="k") is None
    # A flipped signature byte fails the constant-time comparison.
    flipped = "0" if signature[-1] != "0" else "1"
    assert parse_session_token(f"{payload}.{signature[:-1]}{flipped}", secret="k") is None


def test_expired_token_is_rejected() -> None:
    # A token minted with a past expiry must not resolve, even with a valid
    # signature - the signature is checked, then the expiry.
    raw_user_id = str(uuid.uuid4())
    past = int(time.time()) - 10
    payload = f"{raw_user_id}.{past}"
    signature = hmac.new(b"k", payload.encode(), hashlib.sha256).hexdigest()

    assert parse_session_token(f"{payload}.{signature}", secret="k") is None


def test_wrong_secret_and_malformed_tokens_are_rejected() -> None:
    token = create_session_token(uuid.uuid4(), secret="right", ttl_seconds=60)

    assert parse_session_token(token, secret="wrong") is None
    assert parse_session_token("not-a-token", secret="right") is None
    assert parse_session_token("a.b.c.d", secret="right") is None
    assert parse_session_token(None, secret="right") is None


# ---------------------------------------------------------------------------
# 2. Upload validation (no service, no database)
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    return base64.b64decode(_png_base64())


def test_a_valid_png_is_accepted() -> None:
    content = _png_bytes()
    upload = validate_image_upload("street.png", content, declared_type="image/png")

    assert upload.mime_type == "image/png"
    assert upload.size_bytes == len(content)
    assert upload.width > 0 and upload.height > 0


def test_sniff_mime_type_recognises_images_and_rejects_others() -> None:
    assert sniff_mime_type(_png_bytes()) == "image/png"
    assert sniff_mime_type(b"\xff\xd8\xff\xe0junk") == "image/jpeg"
    assert sniff_mime_type(b"RIFF____WEBPVP8 ") == "image/webp"
    assert sniff_mime_type(b"%PDF-1.7") is None
    assert sniff_mime_type(b"") is None


def test_a_declared_type_that_contradicts_the_bytes_is_rejected() -> None:
    with pytest.raises(UploadValidationError):
        validate_image_upload("street.png", _png_bytes(), declared_type="image/jpeg")


def test_a_non_image_is_rejected() -> None:
    with pytest.raises(UploadValidationError):
        validate_image_upload("notes.txt", b"just some text, not an image")


def test_an_oversized_upload_is_rejected() -> None:
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (settings.max_upload_bytes + 1)

    with pytest.raises(UploadValidationError):
        validate_image_upload("huge.png", oversized)


def test_an_empty_upload_is_rejected() -> None:
    with pytest.raises(UploadValidationError):
        validate_image_upload("empty.png", b"")


# ---------------------------------------------------------------------------
# 3. Authentication endpoints
# ---------------------------------------------------------------------------


def test_me_without_a_session_is_unauthorised(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/api/auth/me")

    assert response.status_code == 401


def test_me_returns_the_signed_in_profile(as_citizen, citizen_user, client: TestClient) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(citizen_user.id)
    assert body["role"] == citizen_user.role.value
    # No credential or token is ever returned.
    assert "password" not in body
    assert "password_hash" not in body


def test_demo_accounts_are_listed_in_development(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "environment", "development")

    response = anonymous_client.get("/api/auth/demo-accounts")

    assert response.status_code == 200
    accounts = response.json()
    assert {account["role"] for account in accounts} == {"citizen", "officer", "admin"}
    assert all(account["password"] for account in accounts)


def test_demo_accounts_are_hidden_outside_development(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "environment", "production")

    response = anonymous_client.get("/api/auth/demo-accounts")

    assert response.status_code == 404


def test_login_rejects_unknown_fields(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        "/api/auth/login", json={"email": "a@b.test", "password": "x", "admin": True}
    )

    assert response.status_code == 422


@requires_database
@requires_citizen
def test_login_rejects_an_unknown_address(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        "/api/auth/login", json={"email": "nobody@civicbridge.prototype", "password": "x"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "the email address or password is incorrect"


@requires_database
@requires_citizen
def test_login_rejects_a_wrong_password(anonymous_client: TestClient, citizen_user) -> None:
    response = anonymous_client.post(
        "/api/auth/login",
        json={"email": citizen_user.email, "password": "definitely-not-the-password"},
    )

    assert response.status_code == 401
    # Identical to the unknown-address message: the two cannot be told apart.
    assert response.json()["detail"] == "the email address or password is incorrect"


@requires_database
@requires_citizen
def test_login_then_logout_ends_the_session(anonymous_client: TestClient, citizen_user) -> None:
    login = anonymous_client.post(
        "/api/auth/login",
        json={"email": citizen_user.email, "password": settings.auth_citizen_password},
    )

    assert login.status_code == 200
    body = login.json()
    assert body["user"]["id"] == str(citizen_user.id)
    assert body["expires_in_seconds"] == settings.session_ttl_seconds
    assert "password_hash" not in str(body)
    assert "token" not in str(body).lower()

    # The signed cookie now resolves the caller server-side.
    assert anonymous_client.get("/api/auth/me").status_code == 200

    logout = anonymous_client.post("/api/auth/logout")
    assert logout.status_code == 200
    # Signing out clears the cookie, so the caller is anonymous again.
    assert anonymous_client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# 4. Role-based authorisation
# ---------------------------------------------------------------------------


def test_an_anonymous_caller_is_refused_on_officer_routes(
    anonymous_client: TestClient,
) -> None:
    assert anonymous_client.get("/api/officer/complaints").status_code == 401
    assert anonymous_client.get("/api/officer/review-queue").status_code == 401


def test_an_anonymous_caller_is_refused_on_the_citizen_portal(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.post("/api/citizen/complaints", json={"text": GARBAGE})

    assert response.status_code == 401


def test_a_citizen_is_refused_on_officer_routes(as_citizen, client: TestClient) -> None:
    response = client.get("/api/officer/complaints")

    assert response.status_code == 403
    assert "officer" in response.json()["detail"]


def test_an_officer_is_refused_on_the_citizen_portal(as_officer, client: TestClient) -> None:
    response = client.post("/api/citizen/complaints", json={"text": GARBAGE})

    assert response.status_code == 403
    assert "citizen" in response.json()["detail"]


@requires_database
def test_an_administrator_may_use_the_officer_routes(as_admin, client: TestClient) -> None:
    response = client.get("/api/officer/complaints")

    assert response.status_code == 200
    OfficerComplaintPage.model_validate(response.json())


# ---------------------------------------------------------------------------
# 5. The citizen portal (database + deterministic workflow)
# ---------------------------------------------------------------------------


@requires_database
@requires_citizen
def test_a_citizen_submits_a_complaint_verbatim(
    as_citizen,
    citizen_user,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()
    monkeypatch.setattr("app.api.citizens.get_workflow", lambda: workflow)
    request_id = f"req-{uuid.uuid4().hex[:10]}"

    response = client.post(
        "/api/citizen/complaints",
        json={
            "text": GARBAGE,
            "language": ComplaintLanguage.EN.value,
            "request_id": request_id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    parsed = ComplaintSubmissionResponse.model_validate(body)
    # The citizen's words are stored exactly as typed.
    assert parsed.original_text == GARBAGE
    assert parsed.complaint.reference_id.startswith("CB-")
    # The AI output is labelled a suggestion, not a decision.
    assert "suggestions" in parsed.suggestion_notice
    assert parsed.analysis_outcome in {"completed", "awaiting_human_review", "degraded"}
    _purge(parsed.complaint.complaint_id)


@requires_database
@requires_citizen
def test_a_repeated_request_id_is_not_recorded_twice(
    as_citizen,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()
    monkeypatch.setattr("app.api.citizens.get_workflow", lambda: workflow)
    request_id = f"req-{uuid.uuid4().hex[:10]}"
    payload = {"text": GARBAGE, "request_id": request_id}

    first = client.post("/api/citizen/complaints", json=payload)
    assert first.status_code == 201
    first_reference = first.json()["complaint"]["reference_id"]

    try:
        second = client.post("/api/citizen/complaints", json=payload)
        assert second.status_code == 201
        body = second.json()
        assert body["replayed"] is True
        assert body["complaint"]["reference_id"] == first_reference
    finally:
        _purge(uuid.UUID(first.json()["complaint"]["complaint_id"]))


@requires_database
@requires_citizen
def test_a_citizen_reads_back_only_their_own_complaint(
    as_citizen,
    citizen_user,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()
    monkeypatch.setattr("app.api.citizens.get_workflow", lambda: workflow)

    created = client.post(
        "/api/citizen/complaints",
        json={"text": GARBAGE, "request_id": f"req-{uuid.uuid4().hex[:10]}"},
    )
    assert created.status_code == 201
    complaint_id = uuid.UUID(created.json()["complaint"]["complaint_id"])
    reference = created.json()["complaint"]["reference_id"]

    try:
        listing = client.get("/api/citizen/complaints")
        assert listing.status_code == 200
        page = CitizenComplaintPage.model_validate(listing.json())
        assert reference in {item.reference_id for item in page.items}

        detail = client.get(f"/api/citizen/complaints/{reference}")
        assert detail.status_code == 200
        CitizenComplaintDetail.model_validate(detail.json())
    finally:
        _purge(complaint_id)


@requires_database
@requires_citizen
@requires_officer
def test_one_citizen_cannot_read_another_citizens_complaint(
    as_citizen, citizen_user, client: TestClient
) -> None:
    session = get_session_factory()()
    other_id = session.execute(
        select(User.id).where(User.role == UserRole.CITIZEN, User.id != citizen_user.id).limit(1)
    ).scalar_one_or_none()
    if other_id is None:
        session.close()
        pytest.skip("a second seeded citizen is required for this check")

    reference = f"CB-TEST-{uuid.uuid4().hex[:8].upper()}"
    complaint = Complaint(
        reference_id=reference,
        citizen_id=other_id,
        description="Another resident's complaint.",
        language=ComplaintLanguage.EN,
        status=ComplaintStatus.SUBMITTED,
        is_synthetic=False,
    )
    session.add(complaint)
    session.commit()
    complaint_id = complaint.id
    session.close()

    try:
        response = client.get(f"/api/citizen/complaints/{reference}")
        # 404, not 403: the endpoint must not confirm that the complaint exists.
        assert response.status_code == 404
    finally:
        _purge(complaint_id)


# ---------------------------------------------------------------------------
# 6. The officer dashboard (database)
# ---------------------------------------------------------------------------


@requires_database
@requires_officer
def test_the_dashboard_lists_complaints_read_only(as_officer, client: TestClient) -> None:
    response = client.get("/api/officer/complaints", params={"limit": 5})

    assert response.status_code == 200
    page = OfficerComplaintPage.model_validate(response.json())
    assert "advisory" in page.suggestion_notice
    assert page.limit == 5
    assert page.returned <= 5


@requires_database
@requires_officer
def test_the_review_queue_lists_only_undecided_complaints(as_officer, client: TestClient) -> None:
    response = client.get("/api/officer/review-queue", params={"limit": 5})

    assert response.status_code == 200
    page = OfficerComplaintPage.model_validate(response.json())
    assert all(item.pending_human_review for item in page.items)


@requires_database
@requires_officer
def test_the_department_directory_is_flagged_prototype_data(as_officer, client: TestClient) -> None:
    response = client.get("/api/officer/departments")

    assert response.status_code == 200
    directory = DepartmentDirectoryPage.model_validate(response.json())
    assert directory.total >= 1
    assert all(item.is_prototype_data for item in directory.items)


@requires_database
@requires_officer
def test_an_unknown_complaint_is_a_404(as_officer, client: TestClient) -> None:
    response = client.get(f"/api/officer/complaints/{uuid.uuid4()}")

    assert response.status_code == 404


@requires_database
@requires_citizen
@requires_officer
def test_the_dashboard_opens_a_freshly_submitted_complaint(
    citizen_user,
    officer_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow()
    monkeypatch.setattr("app.api.citizens.get_workflow", lambda: workflow)
    client = TestClient(app)

    app.dependency_overrides[get_optional_user] = lambda: citizen_user
    try:
        created = client.post(
            "/api/citizen/complaints",
            json={"text": GARBAGE, "request_id": f"req-{uuid.uuid4().hex[:10]}"},
        )
        assert created.status_code == 201
        complaint_id = uuid.UUID(created.json()["complaint"]["complaint_id"])
    finally:
        app.dependency_overrides[get_optional_user] = lambda: officer_user

    try:
        detail = client.get(f"/api/officer/complaints/{complaint_id}")
        assert detail.status_code == 200
        body = OfficerComplaintDetail.model_validate(detail.json())
        assert body.complaint.complaint_id == complaint_id
        # The dashboard names the routes an officer must call to decide, but it
        # never calls them itself.
        assert "duplicate_review" in body.decision_endpoints
        assert "routing_decision" in body.decision_endpoints

        audit = client.get(f"/api/officer/complaints/{complaint_id}/audit")
        assert audit.status_code == 200
        assert audit.json()["reference_id"] == body.complaint.reference_id
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
        _purge(complaint_id)
