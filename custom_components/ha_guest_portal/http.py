from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Account, Settings, Zone
from .security import RateLimiter, random_token, tokens_match, verify_password
from .storage import Session, Store

_LOGGER = logging.getLogger(__name__)
SESSION_COOKIE = "hagp_session"
LOGIN_CSRF_COOKIE = "hagp_login_csrf"


class PortalRuntime:
    def __init__(self, hass: HomeAssistant, settings: Settings, store: Store) -> None:
        self.hass = hass
        self.settings = settings
        self.store = store
        self.limiter = RateLimiter()
        package_dir = Path(__file__).resolve().parent
        environment = Environment(
            loader=FileSystemLoader(package_dir / "templates"),
            autoescape=select_autoescape(("html", "xml")),
            auto_reload=False,
        )
        environment.get_template("base.html")
        self.templates = {
            name: environment.get_template(name)
            for name in ("login.html", "dashboard.html", "confirm.html")
        }
        self.stylesheet = (package_dir / "static" / "app.css").read_text(encoding="utf-8")

    def url(self, suffix: str = "") -> str:
        return f"{self.settings.base_path}{suffix}"

    def html(self, template_name: str, **context: Any) -> web.Response:
        body = self.templates[template_name].render(
            title=self.settings.title,
            base_path=self.settings.base_path,
            duration_minutes=self.settings.duration_minutes,
            **context,
        )
        return web.Response(
            text=body,
            content_type="text/html",
            charset="utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'self'; img-src 'self'; "
                    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )

    async def principal(self, request: web.Request) -> tuple[Account, Session] | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        session = await self.hass.async_add_executor_job(self.store.get_session, token)
        if session is None or session.username != self.settings.account.username:
            return None
        return self.settings.account, session

    async def audit(
        self,
        request: web.Request,
        *,
        username: str,
        action_id: str,
        outcome: str,
        detail: str,
    ) -> None:
        await self.hass.async_add_executor_job(
            partial(
                self.store.log,
                username=username,
                action_id=action_id,
                outcome=outcome,
                detail=detail,
                remote_ip=request.remote or "unknown",
            )
        )

    async def stop_all(self) -> None:
        await self.hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": [zone.entity_id for zone in self.settings.zones.values()]},
            blocking=True,
        )

    async def start_zone(self, zone: Zone) -> None:
        await self.stop_all()
        await asyncio.sleep(1)
        await self.hass.services.async_call(
            "rachio",
            "start_multiple_zone_schedule",
            {"entity_id": [zone.entity_id], "duration": [self.settings.duration_minutes]},
            blocking=True,
        )


class PortalView(HomeAssistantView):
    requires_auth = False

    def __init__(self, runtime: PortalRuntime, suffix: str, name: str) -> None:
        self.runtime = runtime
        self.url = runtime.url(suffix)
        self.name = f"api:ha_guest_portal:{name}"


class RootView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "", "root")

    async def get(self, request: web.Request) -> web.StreamResponse:
        principal = await self.runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=self.runtime.url("/login"))
        account, session = principal
        available, reason = self.runtime.settings.access(datetime.now(UTC))
        return self.runtime.html(
            "dashboard.html",
            account=account,
            zones=self.runtime.settings.zones.values(),
            available=available,
            reason=reason,
            csrf_token=session.csrf_token,
            result=request.query.get("result"),
        )


class RootSlashView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/", "root_slash")

    async def get(self, request: web.Request) -> web.StreamResponse:
        return web.HTTPMovedPermanently(location=self.runtime.url())


class LoginView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/login", "login")

    async def get(self, request: web.Request) -> web.StreamResponse:
        if await self.runtime.principal(request):
            return web.HTTPSeeOther(location=self.runtime.url())
        csrf_token = random_token()
        response = self.runtime.html(
            "login.html",
            csrf_token=csrf_token,
            error=request.query.get("error"),
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            csrf_token,
            max_age=600,
            httponly=True,
            secure=self.runtime.settings.cookie_secure,
            samesite="Strict",
            path=self.runtime.settings.base_path,
        )
        return response

    async def post(self, request: web.Request) -> web.StreamResponse:
        form = await request.post()
        username = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        csrf_token = str(form.get("csrf_token", ""))
        if not tokens_match(csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE)):
            return web.HTTPSeeOther(location=self.runtime.url("/login?error=invalid_request"))
        if not self.runtime.limiter.allow(f"login:{username}", limit=5, window_seconds=300):
            return web.HTTPSeeOther(location=self.runtime.url("/login?error=try_later"))
        account = self.runtime.settings.account
        if username != account.username or not verify_password(account.password_hash, password):
            await self.runtime.audit(
                request,
                username=username or "unknown",
                action_id="login",
                outcome="denied",
                detail="invalid credentials",
            )
            return web.HTTPSeeOther(location=self.runtime.url("/login?error=invalid_credentials"))

        expires_at = datetime.now(UTC) + timedelta(hours=self.runtime.settings.session_hours)
        token, _session = await self.runtime.hass.async_add_executor_job(
            self.runtime.store.create_session, account.username, expires_at
        )
        await self.runtime.audit(
            request,
            username=account.username,
            action_id="login",
            outcome="success",
            detail="session created",
        )
        response = web.HTTPSeeOther(location=self.runtime.url())
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=self.runtime.settings.session_hours * 3600,
            httponly=True,
            secure=self.runtime.settings.cookie_secure,
            samesite="Strict",
            path=self.runtime.settings.base_path,
        )
        response.del_cookie(LOGIN_CSRF_COOKIE, path=self.runtime.settings.base_path)
        return response


class ConfirmView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/confirm/{action_id}", "confirm")

    async def get(self, request: web.Request, action_id: str) -> web.StreamResponse:
        principal = await self.runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=self.runtime.url("/login"))
        zone = self.runtime.settings.zones.get(action_id)
        if action_id != "stop_all" and zone is None:
            raise web.HTTPNotFound()
        available, reason = self.runtime.settings.access(datetime.now(UTC))
        return self.runtime.html(
            "confirm.html",
            action_id=action_id,
            action_name="Stop all water" if action_id == "stop_all" else zone.name,
            available=available,
            reason=reason,
            csrf_token=principal[1].csrf_token,
        )


class ActionView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/actions/{action_id}", "actions")

    async def post(self, request: web.Request, action_id: str) -> web.StreamResponse:
        principal = await self.runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=self.runtime.url("/login"))
        account, session = principal
        form = await request.post()
        if not tokens_match(str(form.get("csrf_token", "")), session.csrf_token):
            await self.runtime.audit(
                request,
                username=account.username,
                action_id=action_id,
                outcome="denied",
                detail="csrf validation failed",
            )
            return web.HTTPSeeOther(location=self.runtime.url("?result=denied"))
        zone = self.runtime.settings.zones.get(action_id)
        if action_id != "stop_all" and zone is None:
            return web.HTTPSeeOther(location=self.runtime.url("?result=denied"))
        available, reason = self.runtime.settings.access(datetime.now(UTC))
        if not available:
            await self.runtime.audit(
                request,
                username=account.username,
                action_id=action_id,
                outcome="denied",
                detail=reason,
            )
            return web.HTTPSeeOther(location=self.runtime.url("?result=denied"))
        if not self.runtime.limiter.allow(f"action:{account.username}", limit=1, window_seconds=3):
            return web.HTTPSeeOther(location=self.runtime.url("?result=rate_limited"))
        try:
            if action_id == "stop_all":
                await self.runtime.stop_all()
            else:
                await self.runtime.start_zone(zone)
        except Exception as exc:  # Home Assistant reports the underlying service error.
            _LOGGER.exception("Guest portal action %s failed", action_id)
            await self.runtime.audit(
                request,
                username=account.username,
                action_id=action_id,
                outcome="error",
                detail=type(exc).__name__,
            )
            return web.HTTPSeeOther(location=self.runtime.url("?result=error"))
        await self.runtime.audit(
            request,
            username=account.username,
            action_id=action_id,
            outcome="success",
            detail="command accepted",
        )
        return web.HTTPSeeOther(location=self.runtime.url("?result=success"))


class LogoutView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/logout", "logout")

    async def post(self, request: web.Request) -> web.StreamResponse:
        principal = await self.runtime.principal(request)
        token = request.cookies.get(SESSION_COOKIE)
        form = await request.post()
        if (
            principal
            and token
            and tokens_match(str(form.get("csrf_token", "")), principal[1].csrf_token)
        ):
            await self.runtime.hass.async_add_executor_job(self.runtime.store.delete_session, token)
        response = web.HTTPSeeOther(location=self.runtime.url("/login"))
        response.del_cookie(SESSION_COOKIE, path=self.runtime.settings.base_path)
        return response


class StaticView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/static/app.css", "static")

    async def get(self, request: web.Request) -> web.StreamResponse:
        return web.Response(
            text=self.runtime.stylesheet,
            content_type="text/css",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )


class HealthView(PortalView):
    def __init__(self, runtime: PortalRuntime) -> None:
        super().__init__(runtime, "/healthz", "health")

    async def get(self, request: web.Request) -> web.StreamResponse:
        return web.json_response({"status": "ok"})


def register_views(hass: HomeAssistant, runtime: PortalRuntime) -> None:
    for view in (
        RootView(runtime),
        RootSlashView(runtime),
        LoginView(runtime),
        ConfirmView(runtime),
        ActionView(runtime),
        LogoutView(runtime),
        StaticView(runtime),
        HealthView(runtime),
    ):
        hass.http.register_view(view)
