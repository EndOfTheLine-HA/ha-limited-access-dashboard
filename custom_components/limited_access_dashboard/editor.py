"""Owner-only HA editor and restricted guest presentation/card APIs."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import voluptuous as vol
from aiohttp import web
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import KEY_HASS
from homeassistant.exceptions import Unauthorized

from .config import DOMAIN, URL_ROOT
from .design import default_design, reconcile_design, validate_design, validate_resources
from .http import ManagedView
from .media import action_data
from .security import tokens_match

_LOGGER = logging.getLogger(__name__)


async def read_json(request, maximum: int):
    body = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        body.extend(chunk)
        if len(body) > maximum:
            raise web.HTTPRequestEntityTooLarge(max_size=maximum, actual_size=len(body))
    try:
        return json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise web.HTTPBadRequest(text="Invalid JSON") from exc


async def design_document(runtime):
    owner = await runtime.hass.async_add_executor_job(runtime.store.get_design, "owner")
    has_owner_design = owner is not None
    entities = [card.entity_id for card in runtime.settings.cards]
    owner = owner or {
        "design": default_design(entities),
        "resources": [],
        "allow_guest_customization": runtime.settings.allow_guest_customization,
    }
    if runtime.settings.initial_design and not has_owner_design:
        try:
            owner["design"] = reconcile_design(runtime.settings.initial_design, entities)
        except (ValueError, TypeError):
            pass
    owner["allow_guest_customization"] = runtime.settings.allow_guest_customization
    owner["design"] = reconcile_design(owner["design"], entities)
    return owner


def require_owner(user):
    if user is None or not user.is_owner:
        raise Unauthorized


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/audit", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def websocket_audit(hass, connection, msg):
    require_owner(connection.user)
    runtime = next((r for r in hass.data[DOMAIN].runtimes() if r.entry_id == msg["entry_id"]), None)
    if runtime is None:
        connection.send_error(msg["id"], "not_found", "Dashboard not found")
        return
    rows = await hass.async_add_executor_job(runtime.store.recent_audit)
    connection.send_result(msg["id"], rows)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/editor",
        vol.Optional("entry_id"): str,
        vol.Optional("document"): dict,
    }
)
@websocket_api.async_response
async def websocket_editor(hass, connection, msg):
    require_owner(connection.user)
    manager = hass.data[DOMAIN]
    if "entry_id" not in msg:
        connection.send_result(
            msg["id"],
            [
                {
                    "entry_id": runtime.entry_id,
                    "title": runtime.settings.title,
                    "url": runtime.settings.base_path,
                }
                for runtime in manager.runtimes()
            ],
        )
        return
    runtime = next((r for r in manager.runtimes() if r.entry_id == msg["entry_id"]), None)
    if runtime is None:
        connection.send_error(msg["id"], "not_found", "Dashboard not found")
        return
    if "document" in msg:
        raw = msg["document"]
        try:
            document = {
                "design": validate_design(
                    raw.get("design"), [c.entity_id for c in runtime.settings.cards]
                ),
                "resources": validate_resources(raw.get("resources", [])),
                "allow_guest_customization": raw.get("allow_guest_customization") is True,
            }
        except (ValueError, TypeError, RecursionError) as exc:
            connection.send_error(msg["id"], "invalid_design", str(exc))
            return
        await hass.async_add_executor_job(runtime.store.save_design, "owner", document)
        entry = hass.config_entries.async_get_entry(runtime.entry_id)
        if entry is not None:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    "allow_guest_customization": document["allow_guest_customization"],
                },
            )
        runtime.settings = replace(
            runtime.settings, allow_guest_customization=document["allow_guest_customization"]
        )
    document = await design_document(runtime)
    connection.send_result(
        msg["id"],
        {
            **document,
            "entities": runtime.cards(),
            "url": runtime.settings.base_path,
        },
    )


class EditorStaticView(ManagedView):
    def __init__(self, manager):
        super().__init__(manager, f"{URL_ROOT}/editor-assets/{{filename}}", "editor_assets")

    async def get(self, request, filename):
        if filename not in {"editor-panel.js", "designer.js", "cards.js"}:
            raise web.HTTPNotFound()
        content = await request.app[KEY_HASS].async_add_executor_job(
            (Path(__file__).parent / "static" / filename).read_text, "utf-8"
        )
        return web.Response(
            text=content,
            content_type="text/javascript",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


class DesignView(ManagedView):
    def __init__(self, manager):
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/api/design", "design")

    async def get(self, request, slug):
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            raise web.HTTPUnauthorized()
        owner = await design_document(runtime)
        available = runtime.settings.access(datetime.now(UTC))[0]
        design = owner["design"]
        if owner["allow_guest_customization"]:
            guest = await runtime.hass.async_add_executor_job(runtime.store.get_design, "guest")
            if guest:
                try:
                    design = reconcile_design(
                        guest,
                        [c.entity_id for c in runtime.settings.cards],
                        approved=owner["design"],
                    )
                except (ValueError, TypeError):
                    pass
        return web.json_response(
            {
                **owner,
                "design": design,
                "entities": runtime.cards() if available else [],
                "csrf_token": principal[1].csrf_token,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def post(self, request, slug):
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            raise web.HTTPUnauthorized()
        owner = await design_document(runtime)
        if (
            not owner["allow_guest_customization"]
            or not runtime.settings.access(datetime.now(UTC))[0]
            or not tokens_match(request.headers.get("X-CSRF-Token", ""), principal[1].csrf_token)
        ):
            raise web.HTTPForbidden()
        if not runtime.limiter.allow("design", limit=10, window_seconds=60):
            raise web.HTTPTooManyRequests()
        if request.content_length and request.content_length > 131072:
            raise web.HTTPRequestEntityTooLarge(max_size=131072, actual_size=request.content_length)
        try:
            design = validate_design(
                await read_json(request, 131072),
                [c.entity_id for c in runtime.settings.cards],
                approved=owner["design"],
            )
        except (ValueError, TypeError, RecursionError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        await runtime.hass.async_add_executor_job(runtime.store.save_design, "guest", design)
        await runtime.audit(
            request,
            username=principal[0].username,
            action_id="design",
            outcome="success",
            detail="guest appearance saved",
        )
        return web.json_response({"saved": True})


async def call_restricted_service(runtime, domain, service, data):
    """One entity per call; never pass arbitrary targets/data from a custom card."""
    if not isinstance(data, dict):
        raise ValueError("Service data must be an object")
    entity = data.get("entity_id")
    if isinstance(entity, list) and len(entity) == 1:
        entity = entity[0]
    card = runtime.get_card(entity) if isinstance(entity, str) else None
    if card is None or card.entity_id.split(".")[0] != domain or card.card_type == "sprinkler":
        raise ValueError("Entity or action is not approved")
    state = runtime.hass.states.get(entity)
    if state is None or state.state in {"unknown", "unavailable"}:
        raise ValueError("Entity is unavailable")
    clean = {"entity_id": entity}
    if domain == "media_player":
        form = {}
        if service == "volume_set":
            form["volume"] = float(data.get("volume_level", -1)) * 100
        clean = action_data(state, service, form)
        if service == "volume_mute" and isinstance(data.get("is_volume_muted"), bool):
            clean["is_volume_muted"] = data["is_volume_muted"]
    elif domain in {"light", "switch", "input_boolean"}:
        if service not in {"turn_on", "turn_off", "toggle"}:
            raise ValueError("Unsupported action")
        # Current controls support on/off. Extra card parameters fail visibly.
        if set(data) - {"entity_id"}:
            raise ValueError("This build supports on/off controls only for this entity")
    elif (domain, service) not in {
        ("button", "press"),
        ("scene", "turn_on"),
        ("script", "turn_on"),
    }:
        raise ValueError("Unsupported action")
    elif set(data) - {"entity_id"}:
        raise ValueError("Action parameters are not supported")
    await runtime.hass.services.async_call(domain, service, clean, blocking=True)


class CardServiceView(ManagedView):
    def __init__(self, manager):
        super().__init__(manager, f"{URL_ROOT}/{{slug}}/api/card-service", "card_service")

    async def post(self, request, slug):
        runtime = self.runtime(slug)
        principal = await runtime.principal(request)
        if principal is None:
            raise web.HTTPUnauthorized()
        if not runtime.settings.access(datetime.now(UTC))[0] or not tokens_match(
            request.headers.get("X-CSRF-Token", ""), principal[1].csrf_token
        ):
            raise web.HTTPForbidden()
        if not runtime.limiter.allow(f"action:{principal[0].username}", limit=1, window_seconds=2):
            raise web.HTTPTooManyRequests()
        try:
            data = await read_json(request, 4096)
            if not isinstance(data, dict):
                raise ValueError("Service request must be an object")
            await call_restricted_service(runtime, data["domain"], data["service"], data["data"])
        except (ValueError, KeyError, TypeError) as exc:
            await runtime.audit(
                request,
                username=principal[0].username,
                action_id="custom_card",
                outcome="denied",
                detail="unsupported entity or action",
            )
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except web.HTTPException:
            raise
        except Exception as exc:
            _LOGGER.exception("Limited Access Dashboard custom-card action failed")
            await runtime.audit(
                request,
                username=principal[0].username,
                action_id="custom_card",
                outcome="error",
                detail=f"{data['domain']}.{data['service']} {data['data'].get('entity_id')} "
                f"{type(exc).__name__}",
            )
            raise web.HTTPBadGateway(text="Home Assistant could not complete the action") from exc
        await runtime.audit(
            request,
            username=principal[0].username,
            action_id="custom_card",
            outcome="success",
            detail=f"{data['domain']}.{data['service']} {data['data'].get('entity_id')}",
        )
        return web.json_response({"success": True})


def register_editor(hass, manager):
    websocket_api.async_register_command(hass, websocket_editor)
    websocket_api.async_register_command(hass, websocket_audit)
    for view in (EditorStaticView(manager), DesignView(manager), CardServiceView(manager)):
        hass.http.register_view(view)
    frontend.async_register_built_in_panel(
        hass,
        "custom",
        sidebar_title="Guest dashboard editor",
        sidebar_icon="mdi:view-dashboard-edit",
        frontend_url_path="guest-dashboard-editor",
        require_admin=True,
        config={
            "_panel_custom": {
                "name": "lad-editor-panel",
                "embed_iframe": False,
                "module_url": f"{URL_ROOT}/editor-assets/editor-panel.js",
            }
        },
    )
