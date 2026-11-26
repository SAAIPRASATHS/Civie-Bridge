"""Authentication endpoints (Phase 7/8).

    POST /api/auth/login    -> LoginResponse (sets the session cookie)
    POST /api/auth/register -> LoginResponse for a new citizen account
    POST /api/auth/logout   -> clears the session cookie
    GET  /api/auth/me       -> UserProfile for the signed-in caller
    GET  /api/auth/demo-accounts -> the synthetic prototype accounts

The session is an HMAC-signed, HttpOnly cookie: JavaScript cannot read it, so a
cross-site scripting bug cannot exfiltrate a token, and every protected endpoint
re-resolves the account server-side. Passwords are verified with scrypt against
the stored hash; the stored hash is never returned.

Login answers with one generic message whether or not the address exists, and a
dummy verification runs for unknown accounts so the response time does not reveal
which emails are registered.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import create_session_token, hash_password, verify_password
from app.models.enums import ComplaintLanguage, UserRole
from app.models.user import User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: A throwaway hash used to keep the timing of a failed login indistinguishable
#: from a successful one. Computed once at import.
_DUMMY_HASH = hash_password("not-a-real-password-" + uuid.uuid4().hex)

#: The shortest password a self-registered citizen may choose. A floor, not a
#: policy: the prototype does not enforce composition rules.
MIN_PASSWORD_LENGTH = 8


class LoginRequest(BaseModel):
    """Credentials for the prototype sign-in."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(description="The account email address.")
    password: str = Field(min_length=1, max_length=256, description="The account password.")


class RegisterRequest(BaseModel):
    """A citizen's self-registration.

    Only the citizen role can be created here; officers and administrators are
    provisioned out of band, so a public caller cannot grant themselves a role.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(description="The account email address.")
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=256,
        description="The account password; stored only as a salted scrypt hash.",
    )
    full_name: str = Field(min_length=2, max_length=150, description="The citizen's name.")
    preferred_language: ComplaintLanguage = Field(
        default=ComplaintLanguage.EN,
        description="The language the citizen prefers to be answered in.",
    )


class UserProfile(BaseModel):
    """The caller's own account details. Never includes a hash or a token."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    preferred_language: ComplaintLanguage
    is_active: bool

    @classmethod
    def of(cls, user: User) -> UserProfile:
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            preferred_language=user.preferred_language,
            is_active=user.is_active,
        )


class LoginResponse(BaseModel):
    """The signed-in account plus the session's lifetime in seconds."""

    model_config = ConfigDict(extra="forbid")

    user: UserProfile
    expires_in_seconds: int


class DemoAccount(BaseModel):
    """One synthetic prototype account, exposed so a reviewer can sign in."""

    model_config = ConfigDict(extra="forbid")

    role: UserRole
    email: str
    password: str
    full_name: str
    note: str


def _set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    token = create_session_token(
        user_id,
        secret=settings.session_secret_key,
        ttl_seconds=settings.session_ttl_seconds,
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Sign in and start a session",
    description=(
        "Verifies the credentials against the stored scrypt hash and, on success, "
        "sets an HttpOnly session cookie. The response never contains a token, a "
        "hash or another account's details, and a failure is reported identically "
        "whether the address exists or the password was wrong."
    ),
)
def login(payload: LoginRequest, response: Response, session: SessionDep) -> LoginResponse:
    user = session.scalars(
        select(User).where(User.email == payload.email.strip().lower())
    ).one_or_none()

    if user is None:
        # Burn the same work as a real comparison so timing does not enumerate users.
        verify_password(payload.password, _DUMMY_HASH)
        logger.info("Login rejected for an unknown address")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="the email address or password is incorrect",
        )

    if not verify_password(payload.password, user.password_hash):
        logger.info("Login rejected for account %s", user.id)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="the email address or password is incorrect",
        )

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="this account is not active")

    _set_session_cookie(response, user.id)
    logger.info("Session started for account %s (role=%s)", user.id, user.role.value)
    return LoginResponse(user=UserProfile.of(user), expires_in_seconds=settings.session_ttl_seconds)


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a citizen account",
    description=(
        "Creates a citizen portal account and signs it in. Only the citizen "
        "role can be self-registered. The password is stored as a salted scrypt "
        "hash and is never returned. An address that is already registered is "
        "refused with 409."
    ),
)
def register(payload: RegisterRequest, response: Response, session: SessionDep) -> LoginResponse:
    email = payload.email.strip().lower()
    existing = session.scalars(select(User).where(User.email == email)).one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="that email address is already registered",
        )

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role=UserRole.CITIZEN,
        preferred_language=payload.preferred_language,
        is_active=True,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        # A concurrent registration won the race on the unique email index.
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="that email address is already registered",
        ) from exc
    session.refresh(user)

    _set_session_cookie(response, user.id)
    logger.info("Registered citizen account %s", user.id)
    return LoginResponse(user=UserProfile.of(user), expires_in_seconds=settings.session_ttl_seconds)


@router.post(
    "/logout",
    summary="End the session",
    description="Clears the session cookie. Always succeeds, signed in or not.",
)
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    return {"message": "signed out"}


@router.get(
    "/me",
    response_model=UserProfile,
    summary="The signed-in caller",
    description="Returns the caller's own profile, or 401 when there is no valid session.",
)
def read_me(user: CurrentUser) -> UserProfile:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="authentication is required")
    return UserProfile.of(user)


@router.get(
    "/demo-accounts",
    response_model=list[DemoAccount],
    summary="Synthetic prototype sign-in details",
    description=(
        "Lists the synthetic demo accounts and their known passwords so the "
        "prototype can be reviewed without a real account. Disabled in any "
        "non-development environment and contains no real personal information."
    ),
)
def demo_accounts() -> list[DemoAccount]:
    if settings.environment.lower() not in {"development", "test"}:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="this endpoint is disabled in this environment"
        )
    return [
        DemoAccount(
            role=UserRole.CITIZEN,
            email="citizen001@civicbridge.prototype",
            password=settings.auth_citizen_password,
            full_name="Synthetic Citizen 001",
            note="Citizen portal: submit a complaint and track its status.",
        ),
        DemoAccount(
            role=UserRole.OFFICER,
            email="officer.swm1@civicbridge.prototype",
            password=settings.auth_officer_password,
            full_name="Synthetic SWM Officer 1",
            note="Officer dashboard: review the queue and decide recommendations.",
        ),
        DemoAccount(
            role=UserRole.ADMIN,
            email="admin@civicbridge.prototype",
            password=settings.auth_admin_password,
            full_name="Synthetic Administrator",
            note="Administrator: full officer access.",
        ),
    ]


__all__ = [
    "DemoAccount",
    "LoginRequest",
    "LoginResponse",
    "RegisterRequest",
    "UserProfile",
    "router",
]
