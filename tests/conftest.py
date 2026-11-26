"""Shared pytest fixtures for the CivicBridge test suite.

Phase 7 put server-side session authentication in front of most endpoints. The
fixtures here let a test act as a specific role (officer, citizen or
administrator) without knowing a password.

The caller is resolved from the signed session cookie in production; the tests
override that single dependency (:func:`app.api.deps.get_optional_user`) so a
request is served as a chosen account. This keeps the role checks real - the
guard still runs and still reads the account's role from the database shape -
while removing any dependency on the seeded password hashes.

When PostgreSQL is reachable the override returns a *real* seeded account row,
so foreign keys on the audit trail stay valid. When it is not, a detached
stand-in account is returned; the database-backed tests skip in that case
anyway.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.deps import get_optional_user
from app.database.session import check_database, get_engine, get_session_factory
from app.main import app
from app.models.enums import ComplaintLanguage, UserRole
from app.models.user import User

_DATABASE_STATUS = check_database()


def seeded_user_id(role: UserRole | str) -> uuid.UUID | None:
    """The id of one seeded account with ``role``, or ``None`` when unavailable."""
    if not _DATABASE_STATUS.connected:
        return None
    value = role.value if isinstance(role, UserRole) else role
    try:
        with get_engine().connect() as connection:
            return connection.execute(
                text("SELECT id FROM users WHERE role = :role ORDER BY email LIMIT 1"),
                {"role": value},
            ).scalar_one_or_none()
    except Exception:  # table not migrated yet
        return None


def _account_for(role: UserRole) -> User:
    """A usable account object for ``role``.

    Prefers a real seeded row (so its id satisfies the audit-trail foreign keys)
    and falls back to an in-memory stand-in when PostgreSQL is unavailable.
    """
    user_id = seeded_user_id(role)
    if user_id is not None:
        session = get_session_factory()()
        try:
            stored = session.get(User, user_id)
            if stored is not None:
                session.expunge(stored)
                return stored
        finally:
            session.close()
    return User(
        id=uuid.uuid4(),
        email=f"{role.value}.stand-in@civicbridge.test",
        full_name=f"Stand-in {role.value.title()}",
        password_hash="scrypt$stand-in$not-used",
        role=role,
        preferred_language=ComplaintLanguage.EN,
        is_active=True,
    )


@pytest.fixture()
def officer_user() -> User:
    """An officer (or administrator fallback) account for a guarded request."""
    return _account_for(UserRole.OFFICER)


@pytest.fixture()
def citizen_user() -> User:
    return _account_for(UserRole.CITIZEN)


@pytest.fixture()
def admin_user() -> User:
    return _account_for(UserRole.ADMIN)


def _authenticate(user: User) -> Iterator[User]:
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(get_optional_user, None)


@pytest.fixture()
def as_officer(officer_user: User) -> Iterator[User]:
    """Serve requests as an officer for the duration of one test."""
    yield from _authenticate(officer_user)


@pytest.fixture()
def as_citizen(citizen_user: User) -> Iterator[User]:
    """Serve requests as a citizen for the duration of one test."""
    yield from _authenticate(citizen_user)


@pytest.fixture()
def as_admin(admin_user: User) -> Iterator[User]:
    """Serve requests as an administrator for the duration of one test."""
    yield from _authenticate(admin_user)


@pytest.fixture()
def anonymous_client() -> Iterator[TestClient]:
    """A client with no session at all - the caller is anonymous."""
    app.dependency_overrides.pop(get_optional_user, None)
    yield TestClient(app)
