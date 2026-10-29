"""Optional user accounts: email + password, a signed session cookie.

Everything here is inert until someone registers: an anonymous request never
touches the database (the session is a stateless JWT checked in memory), and the
`users` table is created lazily, like `usage_daily` and the knowledge tables.
With `AUTH_REQUIRED` unset the app behaves exactly as it did before accounts.

Security choices:
  * passwords: stdlib scrypt, a random 16-byte salt per user, the cost
    parameters stored inside the hash string so they can be raised later;
    compared with hmac.compare_digest. The plaintext is never stored or logged.
  * sessions: HS256 JWT signed with SECRET_KEY, 7 days, in an HttpOnly,
    SameSite=Lax cookie (Secure everywhere except localhost). No key => no
    tokens are issued or accepted.
  * login failures are indistinguishable: an unknown email burns the same scrypt
    work as a wrong password, and both return the same 401 body.
  * logout clears the cookie; the token itself is stateless, so a copy stolen
    earlier stays valid until it expires (<= 7 days).
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi import HTTPException, Request

from backend.config import settings
from backend.events.store import _sqlite_path

log = logging.getLogger("aco.auth")

COOKIE_NAME = "aco_session"
_ISSUER = "aco"
_ALGO = "HS256"
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

MIN_PASSWORD = 10
MAX_PASSWORD = 200

_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    " id TEXT PRIMARY KEY,"
    " email TEXT NOT NULL UNIQUE,"
    " password_hash TEXT NOT NULL,"
    " created_at TEXT NOT NULL)"
)


@dataclass(frozen=True)
class User:
    id: str
    email: str


class AccountsUnavailable(RuntimeError):  # noqa: N818
    """The accounts table could not be reached. Mapped to a generic 503."""


# ------------------------------------------------------------ password hashing


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """`scrypt$n$r$p$salt$hash` (base64). Parameters travel with the hash."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p),
            maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


_dummy_hash: str | None = None


def burn_password_check(password: str) -> None:
    """Do the same scrypt work a real check would, for an email that has no
    account, so 'unknown email' and 'wrong password' take alike."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _dummy_hash)


def normalize_email(raw: str) -> str | None:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        return None
    return email


# ------------------------------------------------------------------- sessions


def issue_token(user: User) -> str:
    if not settings.secret_key:
        raise HTTPException(503, "accounts are not configured: set SECRET_KEY on the server")
    now = datetime.now(UTC)
    claims = {
        "iss": _ISSUER, "sub": user.id, "email": user.email,
        "iat": now, "exp": now + timedelta(days=settings.auth_session_days),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=_ALGO)


def read_token(token: str | None) -> User | None:
    """The user a session token names, or None for anything not valid."""
    if not token or not settings.secret_key:
        return None
    try:
        c = jwt.decode(
            token, settings.secret_key, algorithms=[_ALGO], issuer=_ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        return None
    sub, email = c.get("sub"), c.get("email")
    if not isinstance(sub, str) or not isinstance(email, str):
        return None
    return User(id=sub, email=email)


def cookie_secure(request: Request) -> bool:
    """Secure everywhere except plain-http localhost development. Decided by
    host, not scheme: behind a TLS-terminating proxy the scheme reads http."""
    return (request.url.hostname or "") not in _LOCAL_HOSTS


def current_user(request: Request) -> User | None:
    return read_token(request.cookies.get(COOKIE_NAME))


def optional_user(request: Request) -> User | None:
    """Dependency: the signed-in user, or None. Never blocks."""
    return current_user(request)


def gate_user(request: Request) -> User | None:
    """Dependency for the orchestrator endpoints: anonymous is fine unless
    AUTH_REQUIRED is on, in which case a signed-in user is mandatory."""
    user = current_user(request)
    if settings.auth_required and user is None:
        raise HTTPException(401, "sign in required")
    return user


def scope_workspace(user: User | None, workspace_id: str | None) -> str | None:
    """Namespace a knowledge workspace id by user. Anonymous ids pass through
    unchanged. A signed-in user's id becomes an opaque hash of (user, id), so
    another user - or an anonymous caller - can never address it by guessing."""
    if not workspace_id or user is None:
        return workspace_id
    digest = hashlib.sha256(f"{user.id}:{workspace_id}".encode()).hexdigest()
    return "u" + digest[:48]


# --------------------------------------------------------------------- storage

_migrated: set[str] = set()
_mig_lock = threading.Lock()


def _connect():
    url, token = settings.turso_database_url, settings.turso_auth_token
    if not url or url.startswith("file:"):
        path = _sqlite_path(url or "")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=10)
        c.execute("PRAGMA journal_mode = WAL")
        return c, path
    if not token:
        raise RuntimeError("TURSO_AUTH_TOKEN must be set")
    import turso_serverless

    return turso_serverless.connect(url, auth_token=token), url


def _db(fn):
    """Run fn(conn) against the shared database, creating `users` lazily."""
    c, key = _connect()
    with closing(c):
        with _mig_lock:
            if key not in _migrated:
                c.execute(_DDL)
                c.commit()
                _migrated.add(key)
        return fn(c)


def forget() -> None:
    """Test hook: the next call re-checks the table."""
    _migrated.clear()


class DuplicateEmail(Exception):  # noqa: N818
    pass


def _is_unique_violation(e: Exception) -> bool:
    return isinstance(e, sqlite3.IntegrityError) or "unique" in str(e).lower()


async def create_user(email: str, password: str) -> User:
    uid = uuid.uuid4().hex
    # scrypt is CPU-bound (~100ms): off the event loop.
    pw_hash = await asyncio.to_thread(hash_password, password)

    def _do(c):
        c.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            [uid, email, pw_hash, datetime.now(UTC).isoformat()],
        )
        c.commit()

    try:
        await asyncio.to_thread(_db, _do)
    except Exception as e:
        if _is_unique_violation(e):
            raise DuplicateEmail from None
        log.error("could not create user: %s: %s", type(e).__name__, e)
        raise AccountsUnavailable from e
    return User(id=uid, email=email)


async def authenticate(email: str, password: str) -> User | None:
    def _do(c):
        cur = c.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?", [email]
        )
        rows = list(cur)
        c.commit()
        return rows[0] if rows else None

    try:
        row = await asyncio.to_thread(_db, _do)
    except Exception as e:
        log.error("could not read users: %s: %s", type(e).__name__, e)
        raise AccountsUnavailable from e
    if row is None:
        await asyncio.to_thread(burn_password_check, password)
        return None
    ok = await asyncio.to_thread(verify_password, password, row[2])
    return User(id=row[0], email=row[1]) if ok else None
