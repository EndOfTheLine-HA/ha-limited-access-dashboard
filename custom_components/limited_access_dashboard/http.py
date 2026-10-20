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

from .config import URL_ROOT, Account, DashboardCard, Settings
from .security import RateLimiter, random_token, tokens_match, verify_password
from .storage import Session, Store

_LOGGER = logging.getLogger(__name__)
SESSION_COOKIE = "lad_session"
LOGIN_CSRF_COOKIE = "lad_login_csrf"

_DOMAIN_ICONS = {
    "binary_sensor": "◉",
    "button": "●",
    "climate": "°",
    "input_boolean": "◉",
    "light": "◉",
    "scene": "◇",
    "script": "▶",
    "sensor": "◌",
    "switch": "◉",
}


class DashboardRuntime:
    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        settings: Settings,
        store: Store,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
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
        self.script = (package_dir / "static" / "app.js").read_text(encoding="utf-8")

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
                    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
                    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'"
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

    def card_data(self, card: DashboardCard) -> dict[str, Any]:
        state = self.hass.states.get(card.entity_id)
        domain = card.entity_id.split(".", maxsplit=1)[0]
        name = card.entity_id.split(".", maxsplit=1)[1].replace("_", " ").title()
        state_value = "Unavailable"
        unit = ""
        active = False
        if state is not None:
            name = str(state.attributes.get("friendly_name", name))
            state_value = str(state.state).replace("_", " ").title()
            unit = str(state.attributes.get("unit_of_measurement", ""))
            active = state.state == "on"
        if card.card_type == "sprinkler":
            state_value = "Running" if active else "Off"
        return {
            "entity_id": card.entity_id,
            "name": name,
            "state": state_value,
            "unit": unit,
            "active": active,
            "available": state is not None and state.state not in {"unavailable", "unknown"},
            "action": card.action,
            "card_type": card.card_type,
            "icon": "≈" if card.card_type == "sprinkler" else _DOMAIN_ICONS.get(domain, "◉"),
        }

    def cards(self) -> list[dict[str, Any]]:
        return [self.card_data(card) for card in self.settings.cards]

    def get_card(self, entity_id: str) -> DashboardCard | None:
        return next((card for card in self.settings.cards if card.entity_id == entity_id), None)

    async def stop_all_sprinklers(self) -> None:
        entity_ids = [card.entity_id for card in self.settings.sprinklers]
        if entity_ids:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": entity_ids}, blocking=True
            )

    async def start_sprinkler(self, card: DashboardCard) -> None:
        await self.stop_all_sprinklers()
        await asyncio.sleep(1)
        await self.hass.services.async_call(
            "rachio",
            "start_multiple_zone_schedule",
            {
                "entity_id": [card.entity_id],
                "duration": [self.settings.duration_minutes],
            },
            blocking=True,
        )

    async def call_card_action(self, card: DashboardCard) -> None:
        if card.action == "sprinkler":
            await self.start_sprinkler(card)
            return
        domain = card.entity_id.split(".", maxsplit=1)[0]
        if card.action == "toggle":
            await self.hass.services.async_call(
                domain, "toggle", {"entity_id": card.entity_id}, blocking=True
            )
            return
        if card.action == "press":
            await self.hass.services.async_call(
                domain, "press", {"entity_id": card.entity_id}, blocking=True
            )
            return
        if card.action == "turn_on":
            await self.hass.services.async_call(
                domain, "turn_on", {"entity_id": card.entity_id}, blocking=True
            )
            return
        raise ValueError("Card is display-only")


class DashboardManager:
    def __init__(self) -> None:
        self._by_slug: dict[str, DashboardRuntime] = {}
        self._by_entry: dict[str, DashboardRuntime] = {}

    def add(self, runtime: DashboardRuntime) -> None:
        current = self._by_slug.get(runtime.settings.slug)
        if current is not None and current.entry_id != runtime.entry_id:
            raise ValueError(f"Dashboard URL name already in use: {runtime.settings.slug}")
        self.remove(runtime.entry_id)
        self._by_slug[runtime.settings.slug] = runtime
        self._by_entry[runtime.entry_id] = runtime

    def remove(self, entry_id: str) -> None:
        runtime = self._by_entry.pop(entry_id, None)
        if runtime is not None:
            self._by_slug.pop(runtime.settings.slug, None)

    def get(self, slug: str) -> DashboardRuntime | None:
        return self._by_slug.get(slug)

    def only(self) -> DashboardRuntime | None:
        return next(iter(self._by_slug.values())) if len(self._by_slug) == 1 else None


class ManagedView(HomeAssistantView):
    requires_auth = False

    def __init__(self, manager: DashboardManager, url: str, name: str) -> None:
        self.manager = manager
        self.url = url
        self.name = f"api:limited_access_dashboard:{name}"

    def runtime(self, slug: str) -> DashboardRuntime:
        runtime = self.manager.get(slug)
        if runtime is None:
            raise web.HTTPNotFound()
        return runtime


class LandingView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, URL_ROOT, "landing")

    async def get(self, request: web.Request) -> web.StreamResponse:
        runtime = self.manager.only()
        if runtime is None:
            raise web.HTTPNotFound()
        return web.HTTPFound(runtime.url())


class RootView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}", "root")

    async def get(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=runtime.url("/login"))
        account, session = principal
        available, reason = runtime.settings.access(datetime.now(UTC))
        return runtime.html(
            "dashboard.html",
            account=account,
            cards=runtime.cards(),
            has_sprinklers=bool(runtime.settings.sprinklers),
            available=available,
            reason=reason,
            csrf_token=session.csrf_token,
            result=request.query.get("result"),
        )


class LoginView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/login", "login")

    async def get(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        if await runtime.principal(request):
            return web.HTTPSeeOther(location=runtime.url())
        csrf_token = random_token()
        response = runtime.html(
            "login.html", csrf_token=csrf_token, error=request.query.get("error")
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            csrf_token,
            max_age=600,
            httponly=True,
            secure=runtime.settings.cookie_secure,
            samesite="Strict",
            path=runtime.settings.base_path,
        )
        return response

    async def post(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        form = await request.post()
        username = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        csrf_token = str(form.get("csrf_token", ""))
        if not tokens_match(csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE)):
            return web.HTTPSeeOther(location=runtime.url("/login?error=invalid_request"))
        if not runtime.limiter.allow(f"login:{username}", limit=5, window_seconds=300):
            return web.HTTPSeeOther(location=runtime.url("/login?error=try_later"))
        account = runtime.settings.account
        available, _reason = runtime.settings.access(datetime.now(UTC))
        if (
            username != account.username
            or not verify_password(account.password_hash, password)
            or not available
        ):
            await runtime.audit(
                request,
                username=username or "unknown",
                action_id="login",
                outcome="denied",
                detail="invalid credentials or inactive account",
            )
            return web.HTTPSeeOther(location=runtime.url("/login?error=invalid_credentials"))

        expires_at = datetime.now(UTC) + timedelta(hours=runtime.settings.session_hours)
        token, _session = await runtime.hass.async_add_executor_job(
            runtime.store.create_session, account.username, expires_at
        )
        await runtime.audit(
            request,
            username=account.username,
            action_id="login",
            outcome="success",
            detail="session created",
        )
        response = web.HTTPSeeOther(location=runtime.url())
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=runtime.settings.session_hours * 3600,
            httponly=True,
            secure=runtime.settings.cookie_secure,
            samesite="Strict",
            path=runtime.settings.base_path,
        )
        response.del_cookie(LOGIN_CSRF_COOKIE, path=runtime.settings.base_path)
        return response


class ConfirmView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/confirm/{{entity_id}}", "confirm")

    async def get(self, request: web.Request, slug: str, entity_id: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=runtime.url("/login"))
        card = runtime.get_card(entity_id)
        if card is None or card.card_type != "sprinkler":
            raise web.HTTPNotFound()
        available, reason = runtime.settings.access(datetime.now(UTC))
        return runtime.html(
            "confirm.html",
            action_id=entity_id,
            action_name=runtime.card_data(card)["name"],
            available=available,
            reason=reason,
            csrf_token=principal[1].csrf_token,
        )


class ActionView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/actions/{{entity_id}}", "actions")

    async def post(self, request: web.Request, slug: str, entity_id: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=runtime.url("/login"))
        account, session = principal
        form = await request.post()
        if not tokens_match(str(form.get("csrf_token", "")), session.csrf_token):
            await runtime.audit(
                request,
                username=account.username,
                action_id=entity_id,
                outcome="denied",
                detail="csrf validation failed",
            )
            return web.HTTPSeeOther(location=runtime.url("?result=denied"))

        available, reason = runtime.settings.access(datetime.now(UTC))
        card = runtime.get_card(entity_id)
        if not available or card is None or card.action is None:
            await runtime.audit(
                request,
                username=account.username,
                action_id=entity_id,
                outcome="denied",
                detail=reason or "entity or action is not allowlisted",
            )
            return web.HTTPSeeOther(location=runtime.url("?result=denied"))
        if not runtime.limiter.allow(f"action:{account.username}", limit=1, window_seconds=2):
            return web.HTTPSeeOther(location=runtime.url("?result=rate_limited"))
        try:
            await runtime.call_card_action(card)
        except Exception as exc:
            _LOGGER.exception("Limited Access Dashboard action %s failed", entity_id)
            await runtime.audit(
                request,
                username=account.username,
                action_id=entity_id,
                outcome="error",
                detail=type(exc).__name__,
            )
            return web.HTTPSeeOther(location=runtime.url("?result=error"))
        await runtime.audit(
            request,
            username=account.username,
            action_id=entity_id,
            outcome="success",
            detail="command accepted",
        )
        return web.HTTPSeeOther(location=runtime.url("?result=success"))


class StopAllView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/actions/stop-all", "stop_all")

    async def post(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            return web.HTTPSeeOther(location=runtime.url("/login"))
        account, session = principal
        form = await request.post()
        if not runtime.settings.sprinklers or not tokens_match(
            str(form.get("csrf_token", "")), session.csrf_token
        ):
            return web.HTTPSeeOther(location=runtime.url("?result=denied"))
        try:
            await runtime.stop_all_sprinklers()
        except Exception as exc:
            _LOGGER.exception("Limited Access Dashboard stop-all failed")
            await runtime.audit(
                request,
                username=account.username,
                action_id="stop_all",
                outcome="error",
                detail=type(exc).__name__,
            )
            return web.HTTPSeeOther(location=runtime.url("?result=error"))
        await runtime.audit(
            request,
            username=account.username,
            action_id="stop_all",
            outcome="success",
            detail="command accepted",
        )
        return web.HTTPSeeOther(location=runtime.url("?result=success"))


class StateView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/api/state", "state")

    async def get(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        if await runtime.principal(request) is None:
            raise web.HTTPUnauthorized()
        available, reason = runtime.settings.access(datetime.now(UTC))
        return web.json_response(
            {"available": available, "reason": reason, "cards": runtime.cards()},
            headers={"Cache-Control": "no-store"},
        )


class LogoutView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/logout", "logout")

    async def post(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        token = request.cookies.get(SESSION_COOKIE)
        form = await request.post()
        if (
            principal
            and token
            and tokens_match(str(form.get("csrf_token", "")), principal[1].csrf_token)
        ):
            await runtime.hass.async_add_executor_job(runtime.store.delete_session, token)
        response = web.HTTPSeeOther(location=runtime.url("/login"))
        response.del_cookie(SESSION_COOKIE, path=runtime.settings.base_path)
        return response


class StaticView(ManagedView):
    def __init__(self, manager: DashboardManager, filename: str, content_type: str) -> None:
        super().__init__(
            manager,
            f"{URL_ROOT}/{{slug}}/static/{filename}",
            f"static_{filename.replace('.', '_')}",
        )
        self.filename = filename
        self.content_type = content_type

    async def get(self, request: web.Request, slug: str) -> web.StreamResponse:
        runtime = self.runtime(slug)
        content = runtime.stylesheet if self.filename == "app.css" else runtime.script
        return web.Response(
            text=content,
            content_type=self.content_type,
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )


class HealthView(ManagedView):
    def __init__(self, manager: DashboardManager) -> None:
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/healthz", "health")

    async def get(self, request: web.Request, slug: str) -> web.StreamResponse:
        self.runtime(slug)
        return web.json_response({"status": "ok"})


def register_views(hass: HomeAssistant, manager: DashboardManager) -> None:
    for view in (
        LandingView(manager),
        RootView(manager),
        LoginView(manager),
        ConfirmView(manager),
        StopAllView(manager),
        ActionView(manager),
        StateView(manager),
        LogoutView(manager),
        StaticView(manager, "app.css", "text/css"),
        StaticView(manager, "app.js", "text/javascript"),
        HealthView(manager),
    ):
        hass.http.register_view(view)
