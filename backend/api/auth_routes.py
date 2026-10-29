"""/api/auth/*: register, login, logout, me."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend import auth
from backend.config import settings
from backend.limiter import limiter

router = APIRouter(prefix="/auth")

_BAD_LOGIN = "invalid email or password"


class RegisterRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=auth.MIN_PASSWORD, max_length=auth.MAX_PASSWORD)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


def _set_session(request: Request, response: Response, user: auth.User) -> None:
    response.set_cookie(
        auth.COOKIE_NAME, auth.issue_token(user),
        max_age=settings.auth_session_days * 86400, path="/",
        httponly=True, secure=auth.cookie_secure(request), samesite="lax",
    )
    # A response that sets a session must never be cached by a shared proxy.
    response.headers["Cache-Control"] = "no-store"


def _unavailable() -> HTTPException:
    return HTTPException(503, "accounts are temporarily unavailable")


@router.post("/register", status_code=201)
@limiter.limit(lambda: settings.rate_limit_auth)
async def register(body: RegisterRequest, request: Request, response: Response) -> dict:
    if not settings.secret_key:
        raise HTTPException(503, "accounts are not configured: set SECRET_KEY on the server")
    email = auth.normalize_email(body.email)
    if email is None:
        raise HTTPException(422, "enter a valid email address")
    try:
        user = await auth.create_user(email, body.password)
    except auth.DuplicateEmail:
        raise HTTPException(409, "an account with this email already exists") from None
    except auth.AccountsUnavailable:
        raise _unavailable() from None
    _set_session(request, response, user)
    return {"user": {"id": user.id, "email": user.email}}


@router.post("/login")
@limiter.limit(lambda: settings.rate_limit_auth)
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    if not settings.secret_key:
        raise HTTPException(503, "accounts are not configured: set SECRET_KEY on the server")
    email = auth.normalize_email(body.email)
    try:
        # A malformed email takes the same path as an unknown one.
        user = await auth.authenticate(email or "invalid", body.password)
    except auth.AccountsUnavailable:
        raise _unavailable() from None
    if user is None:
        raise HTTPException(401, _BAD_LOGIN)
    _set_session(request, response, user)
    return {"user": {"id": user.id, "email": user.email}}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    response.delete_cookie(
        auth.COOKIE_NAME, path="/", httponly=True,
        secure=auth.cookie_secure(request), samesite="lax",
    )
    return {"ok": True}


@router.get("/me")
async def me(request: Request, response: Response) -> dict:
    user = auth.current_user(request)
    response.headers["Cache-Control"] = "no-store"
    return {
        "user": {"id": user.id, "email": user.email} if user else None,
        "auth_required": settings.auth_required,
    }
