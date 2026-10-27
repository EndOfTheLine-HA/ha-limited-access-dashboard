"""Run with HA's Python. Exercises real HTTP views with fake device services."""

import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, sys.argv[1])
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from homeassistant.core import State  # noqa: E402
from homeassistant.exceptions import Unauthorized  # noqa: E402

from custom_components.limited_access_dashboard.config import (  # noqa: E402
    WeeklySchedule,
    load_settings,
)
from custom_components.limited_access_dashboard.config_flow import _request_is_owner  # noqa: E402
from custom_components.limited_access_dashboard.design import default_design  # noqa: E402
from custom_components.limited_access_dashboard.editor import (  # noqa: E402
    CardServiceView,
    DesignView,
    call_restricted_service,
    require_owner,
    websocket_editor,
)
from custom_components.limited_access_dashboard.http import (  # noqa: E402
    DashboardManager,
    DashboardRuntime,
)
from custom_components.limited_access_dashboard.security import credential_fingerprint  # noqa: E402
from custom_components.limited_access_dashboard.storage import Store  # noqa: E402


async def main():
    with TemporaryDirectory() as directory:
        entities = ["light.guest", "sensor.temperature", "switch.zone"]
        settings = load_settings(
            {
                "username": "guest",
                "slug": "test",
                "password_hash": "$argon2id$test",
                "entities": entities,
                "sprinkler_entities": ["switch.zone"],
                "allow_guest_customization": False,
            }
        )
        states = {
            entity: State(
                entity, "on", {"friendly_name": entity, "private_attribute": "not for guests"}
            )
            for entity in entities
        }

        async def executor(func, *args):
            return func(*args)

        hass = SimpleNamespace(
            async_add_executor_job=executor,
            states=SimpleNamespace(get=states.get),
            services=SimpleNamespace(async_call=AsyncMock()),
            config_entries=SimpleNamespace(async_get_entry=lambda _: None),
        )
        runtime = DashboardRuntime(hass, "test", settings, Store(Path(directory) / "test.db"))
        assert all(
            "private_attribute" not in state["attributes"]
            for state in runtime.restricted_states().values()
        )
        for user in (None, SimpleNamespace(is_owner=False, is_admin=True)):
            try:
                require_owner(user)
            except Unauthorized:
                pass
            else:
                raise AssertionError("Non-owner accepted")
        require_owner(SimpleNamespace(is_owner=True))
        from homeassistant.helpers.http import current_request

        for owner in (True, False):
            token = current_request.set({"hass_user": SimpleNamespace(is_owner=owner)})
            assert _request_is_owner() is owner
            current_request.reset(token)
        await call_restricted_service(runtime, "light", "toggle", {"entity_id": "light.guest"})
        for domain, service, data in [
            ("light", "toggle", {"entity_id": "light.private"}),
            ("light", "toggle", {"entity_id": ["light.guest", "light.private"]}),
            ("sensor", "turn_on", {"entity_id": "sensor.temperature"}),
            ("switch", "turn_on", {"entity_id": "switch.zone"}),
            ("light", "reload", {"entity_id": "light.guest"}),
            ("light", "turn_on", {"entity_id": "light.guest", "profile": "unapproved"}),
        ]:
            hass.services.async_call.reset_mock()
            try:
                await call_restricted_service(runtime, domain, service, data)
            except ValueError:
                pass
            else:
                raise AssertionError("Unauthorized card service accepted")
            hass.services.async_call.assert_not_called()
        manager = DashboardManager()
        manager.add(runtime)
        hass.data = {"limited_access_dashboard": manager}
        connection = SimpleNamespace(
            user=SimpleNamespace(is_owner=True), send_result=Mock(), send_error=Mock()
        )
        await websocket_editor.__wrapped__(hass, connection, {"id": 1})
        assert connection.send_result.call_args.args[1][0]["entry_id"] == "test"
        # Use actual guest cookies, SQLite sessions, CSRF and access checks.
        cookie, session = runtime.store.create_session(
            "guest",
            datetime.now(UTC) + timedelta(hours=1),
            credential_fingerprint(settings.account.password_hash),
        )
        app = web.Application()
        design_view = DesignView(manager)
        card_view = CardServiceView(manager)
        app.router.add_get("/test/api/design", lambda request: design_view.get(request, "test"))
        app.router.add_post("/test/api/design", lambda request: design_view.post(request, "test"))
        app.router.add_post(
            "/test/api/card-service", lambda request: card_view.post(request, "test")
        )
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/test/api/design")).status == 401
            client.session.cookie_jar.update_cookies({"lad_session": cookie})
            response = await client.get("/test/api/design")
            assert response.status == 200
            assert (await response.json())["allow_guest_customization"] is False
            doc = default_design(entities)
            headers = {"X-CSRF-Token": session.csrf_token}
            assert (await client.post("/test/api/design", json=doc, headers=headers)).status == 403
            runtime.settings = replace(settings, allow_guest_customization=True)
            assert (await client.post("/test/api/design", json=doc)).status == 403
            assert (await client.post("/test/api/design", json=doc, headers=headers)).status == 200
            assert runtime.store.get_design("guest") is not None
            bad = deepcopy(doc)
            bad["views"][0]["sections"][0]["cards"].append(
                {"type": "entity", "entity": "light.private"}
            )
            assert (await client.post("/test/api/design", json=bad, headers=headers)).status == 400
            custom = deepcopy(doc)
            custom["views"][0]["sections"][0]["cards"].append(
                {
                    "type": "lovelace",
                    "config": {"type": "custom:unapproved", "entity": "light.guest"},
                }
            )
            assert (
                await client.post("/test/api/design", json=custom, headers=headers)
            ).status == 400
            runtime.settings = replace(runtime.settings, schedule=WeeklySchedule({}))
            assert (await (await client.get("/test/api/design")).json())["entities"] == []
            runtime.settings = replace(runtime.settings, schedule=None)
            runtime.settings = replace(
                runtime.settings,
                account=replace(
                    settings.account, expires_at=datetime.now(UTC) - timedelta(seconds=1)
                ),
            )
            assert (await client.post("/test/api/design", json=doc, headers=headers)).status == 401
            response = await client.post(
                "/test/api/card-service",
                headers=headers,
                json={"domain": "light", "service": "toggle", "data": {"entity_id": "light.guest"}},
            )
            assert response.status == 401
        runtime.store.save_design("owner", {"design": doc, "resources": []})
        assert runtime.store.get_design("guest") is None
        assert json.loads(json.dumps(runtime.store.get_design("owner")))["design"] == doc
    print(
        "PASS: owner authorization, guest opt-in, CSRF, expiry, persistence, "
        "approved states/entities/actions"
    )


asyncio.run(main())
