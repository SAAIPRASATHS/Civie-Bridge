"""Password hashing and signed session tokens (Phase 7).

Two small, dependency-free primitives, both drawn from the standard library so
the prototype adds no new runtime dependency for authentication:

* **Password hashing** - ``hashlib.scrypt`` with a per-account random salt. A
  stored hash is self-describing (``scrypt$n$r$p$salt$hash``) so the cost
  parameters can be raised later without invalidating existing rows.
* **Session tokens** - an HMAC-SHA256 signed ``user_id.expiry.signature``
  string carried in an HttpOnly cookie. The token is *stateless* on purpose:
  the server keeps no session table, so signing out is a matter of clearing the
  cookie and the work factor protects against brute force at login.

Neither function ever logs or returns a password, hash, salt or key, and every
comparison is constant-time (``hmac.compare_digest``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

#: ``hashlib.scrypt`` cost parameters. ``n=2**14, r=8`` needs ~16 MB of memory
#: per hash, which is an affordable floor for a prototype and a real barrier to
#: offline cracking.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
SCRYPT_ALGORITHM = "scrypt"

#: The prefix stamped on every hash produced by this module. Anything without
#: it (for example the Phase 2 ``prototype:no-auth-configured`` placeholder) can
#: never be verified and therefore can never authenticate.
HASH_PREFIX = f"{SCRYPT_ALGORITHM}$"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash for ``password``.

    A fresh random salt is generated per call, so hashing the same password
    twice yields different digests.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("a non-empty password is required")
    salt = os.urandom(SCRYPT_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"{SCRYPT_ALGORITHM}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Constant-time check of ``password`` against a stored hash.

    Returns ``False`` - never raises - for a missing, malformed or placeholder
    hash, so a seed row that predates authentication simply cannot be used to
    sign in rather than crashing the login endpoint.
    """
    if not password or not stored_hash:
        return False
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = stored_hash.split("$")
        if algorithm != SCRYPT_ALGORITHM:
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(derived, expected)


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(user_id: uuid.UUID | str, *, secret: str, ttl_seconds: int) -> str:
    """Build a signed, expiring session token for ``user_id``."""
    expires_at = int(time.time()) + max(1, int(ttl_seconds))
    payload = f"{user_id}.{expires_at}"
    return f"{payload}.{_sign(payload, secret)}"


def parse_session_token(token: str | None, *, secret: str) -> uuid.UUID | None:
    """Return the token's user id, or ``None`` when it is invalid or expired.

    The signature is checked before the expiry is even read, so a forged token
    cannot influence any control flow.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    raw_user_id, raw_expiry, signature = parts
    if not hmac.compare_digest(_sign(f"{raw_user_id}.{raw_expiry}", secret), signature):
        return None
    try:
        expires_at = int(raw_expiry)
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    try:
        return uuid.UUID(raw_user_id)
    except ValueError:
        return None


__all__ = [
    "HASH_PREFIX",
    "create_session_token",
    "hash_password",
    "parse_session_token",
    "verify_password",
]
