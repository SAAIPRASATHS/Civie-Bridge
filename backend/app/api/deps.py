"""FastAPI dependencies for authentication and role-based authorisation.

The browser is never trusted to say who it is: the caller is resolved from the
HMAC-signed session cookie on every request, the account is re-read from the
database, and the role check happens here - on the server - before any handler
body runs. A frontend that hides a button is a convenience, not a control.

Three guards exist:

* :data:`CurrentUser` - resolves the caller, or ``None``.
* :data:`AuthenticatedUser` - any signed-in, active account.
* :data:`OfficerUser` / :data:`AdminUser` - role-restricted.

Every failure returns a bare status code with a short detail; no stack trace,
internal identifier or database message is ever surfaced.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import parse_session_token
from app.database.session import get_session
from app.models.enums import UserRole
from app.models.user import User

logger = get_logger(__name__)

#: ``Annotated`` keeps the ``Depends`` call out of an argument default (ruff B008).
SessionDep = Annotated[Session, Depends(get_session)]

#: Role sets used across the API.
OFFICER_ROLES: frozenset[UserRole] = frozenset({UserRole.OFFICER, UserRole.ADMIN})


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Session"}
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)


def get_optional_user(
    session: SessionDep,
    cookie: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
) -> User | None:
    """Resolve the signed-in account from the session cookie, or ``None``.

    A cookie that is absent, malformed, expired or signed with the wrong key
    resolves to ``None`` - never to an error - so an anonymous caller simply has
    no identity. A database failure is reported as ``503`` rather than being
    mistaken for "not signed in".
    """
    if not cookie:
        return None
    user_id = parse_session_token(cookie, secret=settings.session_secret_key)
    if user_id is None:
        return None
    try:
        user = session.get(User, user_id)
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Session lookup failed: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="the account service is unavailable"
        ) from exc
    if user is None or not user.is_active:
        return None
    return user


#: The caller, or ``None`` when anonymous.
CurrentUser = Annotated[User | None, Depends(get_optional_user)]


def require_user(user: CurrentUser) -> User:
    """Any signed-in, active account."""
    if user is None:
        raise _unauthorised("authentication is required")
    return user


def require_officer(user: Annotated[User, Depends(require_user)]) -> User:
    """An officer or administrator. A citizen is refused."""
    if user.role not in OFFICER_ROLES:
        raise _forbidden("only an officer or administrator may perform this action")
    return user


def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    """An administrator only."""
    if user.role is not UserRole.ADMIN:
        raise _forbidden("only an administrator may perform this action")
    return user


def require_citizen(user: Annotated[User, Depends(require_user)]) -> User:
    """A citizen portal account. An officer uses the officer dashboard instead."""
    if user.role is not UserRole.CITIZEN:
        raise _forbidden("this endpoint is for citizen accounts")
    return user


#: Ready-to-use annotated guards.
AuthenticatedUser = Annotated[User, Depends(require_user)]
OfficerUser = Annotated[User, Depends(require_officer)]
AdminUser = Annotated[User, Depends(require_admin)]
CitizenUser = Annotated[User, Depends(require_citizen)]

__all__ = [
    "OFFICER_ROLES",
    "AdminUser",
    "AuthenticatedUser",
    "CitizenUser",
    "CurrentUser",
    "OfficerUser",
    "SessionDep",
    "get_optional_user",
    "require_admin",
    "require_citizen",
    "require_officer",
    "require_user",
]
