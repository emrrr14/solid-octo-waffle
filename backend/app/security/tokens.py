"""Password hashing and session tokens.

Two token types, deliberately different in kind:

* **Access token** - a short-lived (15 min) signed JWT.  Stateless, so every API
  pod and every websocket handshake can verify it without a database round trip,
  which is what makes the 1 Hz stream cheap to authorise.
* **Refresh token** - a long-lived (30 day) *opaque* random string, stored only
  as a SHA-256 hash.  Not a JWT: a refresh token must be revocable the instant a
  device is lost, and a stateless one cannot be.

Rotation with reuse detection: every refresh mints a new token and marks the old
one used.  If a used token is ever presented again, the family is revoked -
that is the signature of a stolen token being replayed alongside the legitimate
device, and it is the only cheap defence a mobile app has against token theft.

Passwords use ``hashlib.scrypt`` (stdlib, memory-hard).  No bcrypt dependency,
no home-made crypto.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "HS256"

# scrypt parameters: ~64 MB and ~100ms per hash on a modern server.  Costly
# enough to make offline cracking painful, cheap enough for a login endpoint.
SCRYPT_N = 2**16
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LEN = 32
# OpenSSL caps scrypt's memory at 32 MB unless told otherwise, and these
# parameters need ~64 MB (128 * N * r).  Without this the hash raises.
SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * SCRYPT_P + 1024 * 1024


class TokenError(Exception):
    """Any failure to produce a trustworthy identity from a token."""


@dataclass(frozen=True, slots=True)
class TokenSettings:
    secret: str
    issuer: str = "robo-advisor"
    audience: str = "robo-advisor-mobile"
    access_ttl: timedelta = timedelta(minutes=15)
    refresh_ttl: timedelta = timedelta(days=30)
    leeway: timedelta = timedelta(seconds=30)  # phone clocks drift

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: str
    jti: str
    expires_at: datetime


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(digest_b64)),
            maxmem=128 * int(n) * int(r) * int(p) + 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    # Constant-time: a timing side channel on password comparison is a real
    # attack, not a theoretical one.
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


# ---------------------------------------------------------------- access tokens


def issue_access_token(user_id: str, settings: TokenSettings, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iss": settings.issuer,
        "aud": settings.audience,
        "iat": int(now.timestamp()),
        "exp": int((now + settings.access_ttl).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, settings.secret, algorithm=ALGORITHM)


def decode_access_token(token: str, settings: TokenSettings) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.secret,
            algorithms=[ALGORITHM],       # pinned: never trust the token's own alg header
            audience=settings.audience,
            issuer=settings.issuer,
            leeway=settings.leeway,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("typ") != "access":
        # A refresh token presented as a bearer credential is an attack or a
        # client bug; either way it must not authorise a request.
        raise TokenError("wrong token type")

    return AccessClaims(
        user_id=str(payload["sub"]),
        jti=str(payload.get("jti", "")),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )


# ---------------------------------------------------------------- refresh tokens


def new_refresh_token() -> tuple[str, str]:
    """Return ``(token, token_hash)``.  Only the hash is ever persisted."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    # SHA-256, not scrypt: this value is 48 random bytes, so there is nothing to
    # brute-force, and refresh happens on the hot path of every app launch.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_bearer_subprotocol(header: str | None) -> str | None:
    """Pull the token out of ``Sec-WebSocket-Protocol: bearer, <token>``.

    The websocket handshake has no Authorization header in any browser or RN
    implementation, and a token in the query string ends up in proxy logs and
    crash reports.  The subprotocol slot is the standard workaround; the server
    must echo ``bearer`` back on accept or the client rejects the connection.
    """
    if not header:
        return None
    parts = [p.strip() for p in header.split(",")]
    if len(parts) != 2 or parts[0] != "bearer" or not parts[1]:
        return None
    return parts[1]
