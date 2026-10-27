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
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, sys.argv[1])
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from homeassistant.core import State  # noqa: E402
from homeassistant.exceptions import ConfigEntryError, Unauthorized  # noqa: E402

from custom_components.limited_access_dashboard import (  # noqa: E402
    async_remove_entry,
    async_setup_entry,
)
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
    design_document,
    require_owner,
    websocket_audit,
    websocket_editor,
)
from custom_components.limited_access_dashboard.http import (  # noqa: E402
    DashboardManager,
    DashboardRuntime,
)
from custom_components.limited_access_dashboard.security import credential_fingerprint  # noqa: E402
from custom_components.limited_access_dashboard.storage import Store  # noqa: E402


async def check_entry_migration():
    with TemporaryDirectory() as directory:
        config_root = Path(directory) / "core-config"
        legacy = config_root / "limited-access-dashboard-data" / "guest.db"
        old = Store(legacy)
        old.save_design("owner", {"design": default_design(["light.guest"]), "resources": []})
        entry = SimpleNamespace(
            entry_id="ENTRY",
            unique_id="guest",
            title="Guest",
            data={
                "slug": "guest",
                "username": "guest",
                "password_hash": "$argon2id$test",
                "entities": ["light.guest"],
            },
            options={},
        )

        def update(current, **changes):
            for key, value in changes.items():
                setattr(current, key, value)

        async def executor(func, *args):
            return func(*args)

        manager = DashboardManager()
        hass = SimpleNamespace(
            config=SimpleNamespace(
                path=lambda *parts: str(config_root.joinpath(*parts)), time_zone="UTC"
            ),
            async_add_executor_job=executor,
            data={"limited_access_dashboard": manager},
            config_entries=SimpleNamespace(async_update_entry=update),
            states=SimpleNamespace(get=lambda _: None),
        )
        registry = SimpleNamespace(async_get_or_create=Mock())
        with (
            patch("custom_components.limited_access_dashboard.is_sprinkler", return_value=False),
            patch("custom_components.limited_access_dashboard.dr.async_get", return_value=registry),
            patch(
                "custom_components.limited_access_dashboard.dashboard_url", return_value="/guest"
            ),
        ):
            assert await async_setup_entry(hass, entry)
            path = Path(entry.data["database_path"])
            assert path == legacy.parent / "ENTRY.db" and not legacy.exists()
            entry.options = {"slug": "renamed"}
            assert await async_setup_entry(hass, entry)
            assert entry.runtime_data.store.get_design("owner") is not None
            assert entry.runtime_data.settings.database_path == path
            await async_remove_entry(hass, entry)
            assert not path.exists()
            assert not path.with_name(path.name + ".migrated").exists()
            # New UI entries cannot inherit an orphan even when the slug matches.
            old = Store(legacy)
            old.save_design("owner", {"design": default_design(["light.guest"]), "resources": []})
            fresh = SimpleNamespace(
                entry_id="NEW",
                unique_id="guest",
                title="New",
                data={**entry.data, "storage_version": 2},
                options={},
            )
            fresh.data.pop("database_path")
            assert await async_setup_entry(hass, fresh)
            assert fresh.runtime_data.store.get_design("owner") is None
            assert legacy.exists()
            await async_remove_entry(hass, fresh)
            assert legacy.exists()
            imported = SimpleNamespace(
                entry_id="IMPORTED",
                unique_id="imported",
                title="Imported",
                data={
                    **fresh.data,
                    "slug": "imported",
                    "database_path": str(config_root / "not-yet-created.db"),
                    "storage_import": True,
                },
                options={},
            )
            assert await async_setup_entry(hass, imported)
            assert "storage_import" not in imported.data
            assert not (config_root / "not-yet-created.db").exists()
            await async_remove_entry(hass, imported)
            broken = SimpleNamespace(
                entry_id="BROKEN",
                unique_id="broken",
                data={
                    **fresh.data,
                    "slug": "broken",
                    "database_path": str(config_root / "missing.db"),
                },
                options={},
            )
            try:
                await async_setup_entry(hass, broken)
            except ConfigEntryError as exc:
                assert "missing" in str(exc)
            else:
                raise AssertionError("Setup error not exposed to the HA UI")


async def main():
    await check_entry_migration()
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
        login_csp = runtime.html("login.html").headers["Content-Security-Policy"]
        dashboard_csp = runtime.html("dashboard.html", account=settings.account).headers[
            "Content-Security-Policy"
        ]
        assert "unsafe-inline" not in login_csp and "data:" not in login_csp
        assert "unsafe-inline" in dashboard_csp
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
            runtime.store.save_design("guest", {"views": "corrupt"})
            recovered_guest = await (await client.get("/test/api/design")).json()
            assert "design_warning" in recovered_guest
            assert runtime.store.get_design("guest")["views"] == "corrupt"
            service = {"domain": "light", "service": "toggle", "data": {"entity_id": "light.guest"}}
            runtime.limiter = type(runtime.limiter)()
            assert (
                await client.post("/test/api/card-service", json=service, headers=headers)
            ).status == 200
            assert "light.guest" in runtime.store.recent_audit()[0]["detail"]
            runtime.limiter = type(runtime.limiter)()
            denied = {**service, "data": {"entity_id": "light.private"}}
            assert (
                await client.post("/test/api/card-service", json=denied, headers=headers)
            ).status == 400
            assert "light.private" in runtime.store.recent_audit()[0]["detail"]
            runtime.limiter = type(runtime.limiter)()
            hass.services.async_call.side_effect = RuntimeError("private diagnostic")
            failed = await client.post("/test/api/card-service", json=service, headers=headers)
            assert failed.status == 502 and "private diagnostic" not in await failed.text()
            assert runtime.store.recent_audit()[0]["outcome"] == "error"
            assert "light.guest" in runtime.store.recent_audit()[0]["detail"]
            hass.services.async_call.side_effect = None
            runtime.limiter = type(runtime.limiter)()

            async def oversized():
                for _ in range(3):
                    yield b" " * 2048

            assert (
                await client.post("/test/api/card-service", data=oversized(), headers=headers)
            ).status == 413
            await websocket_audit.__wrapped__(hass, connection, {"id": 2, "entry_id": "test"})
            assert connection.send_result.call_args.args[1][0]["outcome"] == "error"
            connection.user = SimpleNamespace(is_owner=False)
            try:
                await websocket_audit.__wrapped__(hass, connection, {"id": 3, "entry_id": "test"})
            except Unauthorized:
                pass
            else:
                raise AssertionError("Non-owner read audit")
            connection.user = SimpleNamespace(is_owner=True)
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
        # Corrupt stored owner documents fall back visibly, without overwriting data.
        runtime.store.save_design("owner", {"design": {"views": "corrupt"}, "resources": []})
        recovered = await design_document(runtime)
        assert recovered["design_warning"]
        assert isinstance(recovered["design"]["views"], list)
        assert runtime.store.get_design("owner")["design"]["views"] == "corrupt"
        runtime.store.save_design("owner", {"design": doc, "resources": []})
        assert runtime.store.get_design("guest") is None
        assert json.loads(json.dumps(runtime.store.get_design("owner")))["design"] == doc
    print(
        "PASS: owner authorization, guest opt-in, CSRF, expiry, persistence, "
        "approved states/entities/actions"
    )


asyncio.run(main())
