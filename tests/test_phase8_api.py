"""Phase 8 API tests: citizen self-registration and dashboard analytics.

Run from the ``backend`` directory:

    python -m pytest ../tests/test_phase8_api.py -v

Two tiers, both needing PostgreSQL:

* **Registration** - ``POST /api/auth/register`` creates a citizen, signs it in,
  refuses a duplicate address, refuses a short password and refuses an attempt
  to self-assign a role. Every created account is removed again so the seeded
  dataset is left untouched.
* **Analytics** - ``GET /api/officer/analytics`` returns aggregate figures that
  add up, and is refused to an anonymous caller (401) and to a citizen (403).

Registration needs no role fixture: it is the one authenticated flow a caller
performs *without* a session, which is exactly what these tests exercise.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.officers import OfficerAnalytics
from app.core.config import settings
from app.database.session import check_database, get_session_factory
from app.main import app
from app.models.user import User

_database_status = check_database()
requires_database = pytest.mark.skipif(
    not _database_status.connected,
    reason="PostgreSQL not reachable; run `python -m app.cli health` first",
)

PASSPHRASE = "a-good-passphrase"


def _unique_email() -> str:
    return f"phase8.{uuid.uuid4().hex[:12]}@civicbridge.prototype"


def _delete_user(email: str) -> None:
    if not _database_status.connected:
        return
    session = get_session_factory()()
    try:
        session.execute(delete(User).where(User.email == email))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def officer_client(as_officer: User) -> TestClient:
    """A client whose requests are served as an officer."""
    return TestClient(app)


@pytest.fixture()
def citizen_client(as_citizen: User) -> TestClient:
    """A client whose requests are served as a citizen."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@requires_database
def test_register_creates_a_citizen_and_signs_in(anonymous_client: TestClient) -> None:
    email = _unique_email()
    try:
        response = anonymous_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": PASSPHRASE,
                "full_name": "Phase Eight Tester",
                "preferred_language": "ta",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["user"]["role"] == "citizen"
        assert body["user"]["email"] == email
        assert body["user"]["preferred_language"] == "ta"
        assert body["user"]["is_active"] is True
        assert "password" not in body["user"]
        assert "password_hash" not in body["user"]
        assert body["expires_in_seconds"] > 0

        # The session cookie is set and is not readable from script.
        assert settings.session_cookie_name in response.cookies
        assert "httponly" in response.headers["set-cookie"].lower()

        # The new session is immediately usable.
        me = anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == email
    finally:
        _delete_user(email)


@requires_database
def test_register_refuses_a_duplicate_address(anonymous_client: TestClient) -> None:
    email = _unique_email()
    payload = {"email": email, "password": PASSPHRASE, "full_name": "First Registration"}
    try:
        first = anonymous_client.post("/api/auth/register", json=payload)
        assert first.status_code == 201, first.text

        again = anonymous_client.post("/api/auth/register", json=payload)
        assert again.status_code == 409
        assert "already registered" in again.json()["detail"]
    finally:
        _delete_user(email)


def test_register_refuses_a_short_password(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        "/api/auth/register",
        json={"email": _unique_email(), "password": "short", "full_name": "Too Short"},
    )
    assert response.status_code == 422


@requires_database
def test_register_refuses_a_caller_chosen_role(anonymous_client: TestClient) -> None:
    """Only the citizen role may be self-registered; ``extra="forbid"`` blocks it."""
    email = _unique_email()
    try:
        response = anonymous_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": PASSPHRASE,
                "full_name": "Wants To Be Admin",
                "role": "admin",
            },
        )
        assert response.status_code == 422
    finally:
        _delete_user(email)


@requires_database
def test_a_registered_citizen_reaches_the_portal_but_not_the_officer_routes(
    anonymous_client: TestClient,
) -> None:
    email = _unique_email()
    try:
        registered = anonymous_client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSPHRASE, "full_name": "Portal Tester"},
        )
        assert registered.status_code == 201, registered.text

        listing = anonymous_client.get("/api/citizen/complaints")
        assert listing.status_code == 200
        assert listing.json()["total"] == 0
        assert listing.json()["items"] == []

        refused = anonymous_client.get("/api/officer/complaints")
        assert refused.status_code == 403
    finally:
        _delete_user(email)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def test_analytics_is_refused_to_an_anonymous_caller(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/api/officer/analytics").status_code == 401


def test_analytics_is_refused_to_a_citizen(citizen_client: TestClient) -> None:
    assert citizen_client.get("/api/officer/analytics").status_code == 403


@requires_database
def test_analytics_figures_add_up(officer_client: TestClient) -> None:
    response = officer_client.get("/api/officer/analytics")
    assert response.status_code == 200, response.text
    body = response.json()

    # The response validates against the declared schema.
    parsed = OfficerAnalytics.model_validate(body)

    assert parsed.total_complaints >= 0
    assert parsed.pending_review + parsed.decided == parsed.total_complaints
    assert sum(bucket.count for bucket in parsed.by_status) == parsed.total_complaints
    # Every complaint has a status and a language; category and priority may be
    # unset, and are surfaced as an explicit "unspecified" bucket.
    assert sum(bucket.count for bucket in parsed.by_category) == parsed.total_complaints
    assert sum(bucket.count for bucket in parsed.by_priority) == parsed.total_complaints
    assert sum(bucket.count for bucket in parsed.by_language) == parsed.total_complaints
    assert parsed.window_days == 30
    assert parsed.provenance


@requires_database
def test_analytics_window_must_stay_in_range(officer_client: TestClient) -> None:
    week = officer_client.get("/api/officer/analytics", params={"window_days": 7})
    assert week.status_code == 200
    assert week.json()["window_days"] == 7

    too_small = officer_client.get("/api/officer/analytics", params={"window_days": 1})
    assert too_small.status_code == 422


@requires_database
def test_analytics_trend_points_are_iso_dated(officer_client: TestClient) -> None:
    response = officer_client.get("/api/officer/analytics")
    assert response.status_code == 200
    for point in response.json()["submitted_trend"]:
        assert point["period"][:4].isdigit()
        assert len(point["period"]) == 10  # YYYY-MM-DD
        assert point["count"] >= 0
