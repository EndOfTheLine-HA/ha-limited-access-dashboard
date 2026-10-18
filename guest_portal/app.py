from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Account, PortalAction, load_settings, session_expiry
from .security import RateLimiter, random_token, tokens_match, verify_password
from .storage import Session, Store

WebhookSender = Callable[[PortalAction, float], Awaitable[int]]
COOKIE_NAME = "hagp_session"
LOGIN_CSRF_COOKIE = "hagp_login_csrf"


async def _send_webhook(action: PortalAction, timeout: float) -> int:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(
            action.webhook_url,
            json=action.payload,
            headers={"User-Agent": "ha-guest-portal/0.1"},
        )
        response.raise_for_status()
        return response.status_code


def create_app(
    config_path: str | Path | None = None,
    *,
    webhook_sender: WebhookSender | None = None,
) -> FastAPI:
    selected_path = config_path or Path("config.yml")
    settings = load_settings(selected_path)
    store = Store(settings.database_path)
    sender = webhook_sender or _send_webhook
    limiter = RateLimiter()
    package_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=package_dir / "templates")

    app = FastAPI(title=settings.title, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=package_dir / "static"), name="static")
    app.state.settings = settings
    app.state.store = store

    def remote_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def current_principal(request: Request) -> tuple[Account, Session] | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        now = datetime.now(UTC)
        session = store.get_session(token, now)
        if session is None:
            return None
        account = settings.accounts.get(session.username)
        if account is None or not account.is_active(now):
            return None
        return account, session

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):  # type: ignore[no-untyped-def]
        if current_principal(request):
            return RedirectResponse("/", status_code=303)
        csrf_token = random_token()
        response = templates.TemplateResponse(
            request,
            "login.html",
            {"title": settings.title, "csrf_token": csrf_token, "error": None},
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            csrf_token,
            max_age=600,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
        )
        return response

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
    ):  # type: ignore[no-untyped-def]
        normalized = username.strip().lower()
        ip = remote_ip(request)
        if not tokens_match(csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE)):
            return RedirectResponse("/login?error=invalid_request", status_code=303)
        if not limiter.allow(f"login:{ip}:{normalized}", limit=5, window_seconds=300):
            return RedirectResponse("/login?error=try_later", status_code=303)
        account = settings.accounts.get(normalized)
        valid_password = verify_password(account.password_hash if account else None, password)
        now = datetime.now(UTC)
        if account is None or not valid_password or not account.is_active(now):
            store.log(
                username=normalized or "unknown",
                action_id="login",
                outcome="denied",
                detail="invalid credentials or inactive account",
                remote_ip=ip,
            )
            return RedirectResponse("/login?error=invalid_credentials", status_code=303)
        token, _session = store.create_session(account.username, session_expiry(settings, now))
        store.log(
            username=account.username,
            action_id="login",
            outcome="success",
            detail="session created",
            remote_ip=ip,
        )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
        )
        response.delete_cookie(LOGIN_CSRF_COOKIE)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):  # type: ignore[no-untyped-def]
        principal = current_principal(request)
        if principal is None:
            return RedirectResponse("/login", status_code=303)
        account, session = principal
        profile = settings.profiles[account.profile_id]
        available, reason = settings.account_access(account, datetime.now(UTC))
        actions = [settings.actions[action_id] for action_id in profile.action_ids]
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "title": settings.title,
                "account": account,
                "profile": profile,
                "actions": actions,
                "available": available,
                "reason": reason,
                "csrf_token": session.csrf_token,
                "result": request.query_params.get("result"),
                "action_name": request.query_params.get("action"),
            },
        )

    @app.get("/confirm/{action_id}", response_class=HTMLResponse)
    async def confirm_action(action_id: str, request: Request):  # type: ignore[no-untyped-def]
        principal = current_principal(request)
        if principal is None:
            return RedirectResponse("/login", status_code=303)
        account, session = principal
        profile = settings.profiles[account.profile_id]
        action = settings.actions.get(action_id)
        available, reason = settings.account_access(account, datetime.now(UTC))
        if action is None or action_id not in profile.action_ids or not available:
            return RedirectResponse("/?result=denied", status_code=303)
        if not action.confirmation:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request,
            "confirm.html",
            {
                "title": settings.title,
                "action": action,
                "csrf_token": session.csrf_token,
                "reason": reason,
            },
        )

    @app.post("/actions/{action_id}")
    async def perform_action(
        action_id: str,
        request: Request,
        csrf_token: str = Form(...),
    ):  # type: ignore[no-untyped-def]
        principal = current_principal(request)
        if principal is None:
            return RedirectResponse("/login", status_code=303)
        account, session = principal
        ip = remote_ip(request)
        if not tokens_match(csrf_token, session.csrf_token):
            store.log(
                username=account.username,
                action_id=action_id,
                outcome="denied",
                detail="csrf validation failed",
                remote_ip=ip,
            )
            return RedirectResponse("/?result=denied", status_code=303)
        profile = settings.profiles[account.profile_id]
        action = settings.actions.get(action_id)
        available, reason = settings.account_access(account, datetime.now(UTC))
        if action is None or action_id not in profile.action_ids or not available:
            store.log(
                username=account.username,
                action_id=action_id,
                outcome="denied",
                detail=reason or "action not assigned to profile",
                remote_ip=ip,
            )
            return RedirectResponse("/?result=denied", status_code=303)
        if not limiter.allow(
            f"action:{account.username}:{action_id}",
            limit=1,
            window_seconds=max(1, action.cooldown_seconds),
        ):
            return RedirectResponse("/?result=rate_limited", status_code=303)
        try:
            status_code = await sender(action, settings.request_timeout_seconds)
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            store.log(
                username=account.username,
                action_id=action_id,
                outcome="error",
                detail=type(exc).__name__,
                remote_ip=ip,
            )
            return RedirectResponse(f"/?result=error&action={quote(action.name)}", status_code=303)
        store.log(
            username=account.username,
            action_id=action_id,
            outcome="success",
            detail=f"webhook returned {status_code}",
            remote_ip=ip,
        )
        return RedirectResponse(f"/?result=success&action={quote(action.name)}", status_code=303)

    @app.post("/logout")
    async def logout(request: Request, csrf_token: str = Form(...)):  # type: ignore[no-untyped-def]
        principal = current_principal(request)
        token = request.cookies.get(COOKIE_NAME)
        if principal and tokens_match(csrf_token, principal[1].csrf_token) and token:
            store.delete_session(token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    return app
