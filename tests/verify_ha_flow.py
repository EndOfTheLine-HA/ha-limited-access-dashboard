"""Run with HA's Python: python tests/verify_ha_flow.py REPOSITORY_ROOT.

Uses fake states/services: never operates real devices or edits HA configuration.
"""

import asyncio
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, sys.argv[1])
from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature  # noqa: E402
from homeassistant.core import State  # noqa: E402
from homeassistant.helpers.network import NoURLAvailableError  # noqa: E402

from custom_components.limited_access_dashboard import config_flow as flow  # noqa: E402
from custom_components.limited_access_dashboard import remove_database  # noqa: E402
from custom_components.limited_access_dashboard.config import (  # noqa: E402
    DashboardCard,
    load_settings,
)
from custom_components.limited_access_dashboard.http import (  # noqa: E402
    LOGIN_CSRF_COOKIE,
    LOGIN_LIMIT_PER_ADDRESS,
    MAX_LOGIN_BODY_BYTES,
    SESSION_COOKIE,
    ActionView,
    DashboardManager,
    DashboardRuntime,
    LoginView,
    StateView,
)
from custom_components.limited_access_dashboard.links import dashboard_url  # noqa: E402
from custom_components.limited_access_dashboard.media import action_data, controls  # noqa: E402
from custom_components.limited_access_dashboard.security import (  # noqa: E402
    RateLimiter,
    hash_password,
    token_hash,
    verify_password,
)
from custom_components.limited_access_dashboard.storage import (  # noqa: E402
    AUDIT_LOG_MAX_ROWS,
    Store,
)


async def check_flows():
    with patch(
        "custom_components.limited_access_dashboard.links.get_url",
        return_value="https://test.ui.nabu.casa/",
    ):
        assert (
            dashboard_url(None, "guest")
            == "https://test.ui.nabu.casa/limited-access-dashboard/guest"
        )
    with patch(
        "custom_components.limited_access_dashboard.links.get_url", side_effect=NoURLAvailableError
    ):
        assert dashboard_url(None, "guest") == "/limited-access-dashboard/guest"
    assert verify_password(hash_password("twelve-chars"), "twelve-chars")
    for rejected in ("eleven-char", "x" * 257):
        try:
            hash_password(rejected)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{len(rejected)}-character password accepted")
    for entity_type in flow.ENTITY_TYPES:
        schema = flow._cards_schema({}, entity_type).schema
        assert "entities" not in schema
        assert "enable_sprinklers" not in schema
        assert "sprinkler_entities" not in schema and "duration_minutes" not in schema
        config = schema["add_entities"].config
        if entity_type == "outlet":
            assert config["filter"][0]["device_class"] == ["outlet"]
        selected_schema = flow._cards_schema(
            {"entities": ["light.porch", "media_player.tv"]}, entity_type
        ).schema
        assert selected_schema["entities"].config["include_entities"] == [
            "light.porch",
            "media_player.tv",
        ]
        assert selected_schema["entities"].config["reorder"] is True
    registry = SimpleNamespace(
        async_get=lambda entity: {
            "switch.zone": SimpleNamespace(platform="rachio", unique_id="controller-zone-id"),
            "switch.schedule": SimpleNamespace(
                platform="rachio", unique_id="controller-schedule-id"
            ),
            "switch.sprinkler_named_plug": SimpleNamespace(platform="tapo", unique_id="plug"),
        }.get(entity)
    )
    with patch(
        "custom_components.limited_access_dashboard.entities.er.async_get", return_value=registry
    ):
        for cls in (flow.LimitedAccessDashboardConfigFlow, flow.LimitedAccessDashboardOptionsFlow):
            item = cls()
            item.handler = "test"
            item.hass = SimpleNamespace(
                config_entries=SimpleNamespace(
                    async_get_known_entry=lambda _: SimpleNamespace(data={}, options={}),
                )
            )
            item.async_step_schedule = AsyncMock(return_value={"step_id": "schedule"})
            assert (await item.async_step_cards({"entities": []}))["errors"]
            result = await item.async_step_cards(
                {"entities": ["light.porch", "switch.sprinkler_named_plug"]}
            )
            assert result["step_id"] == "schedule"
            assert item._data["sprinkler_entities"] == []
            result = await item.async_step_cards(
                {"entities": ["light.porch"], "add_entities": ["switch.zone"]}
            )
            assert result["step_id"] == "sprinklers"
            assert item._data["sprinkler_entities"] == ["switch.zone"]
            assert set(result["data_schema"].schema) == {"duration_minutes"}
            assert (await item.async_step_sprinklers({"duration_minutes": 5}))[
                "step_id"
            ] == "schedule"
            result = await item.async_step_cards({"entities": ["switch.schedule"]})
            assert result["errors"]["entities"] == "unsupported_irrigation"
            result = await item.async_step_cards(
                {"entities": ["light.porch"], "choose_another_type": True}
            )
            assert result["step_id"] == "entity_filter"
            result = await item.async_step_entity_filter({"entity_type": "media_player"})
            assert result["step_id"] == "cards"
            assert result["data_schema"].schema["entities"].config["include_entities"] == [
                "light.porch"
            ]
            assert item._data["entities"] == ["light.porch"]
            result = await item.async_step_cards(
                {"entities": ["light.porch"], "add_entities": ["media_player.tv"]}
            )
            assert result["step_id"] == "schedule"
            assert item._data["entities"] == ["light.porch", "media_player.tv"]
            assert item._data["sprinkler_entities"] == []
            item._data.update(slug="guest", username="visitor", title="Guest")
            with patch(
                "custom_components.limited_access_dashboard.config_flow.dashboard_url",
                return_value="https://example.invalid/limited-access-dashboard/guest",
            ):
                result = await item.async_step_share()
                assert result["description_placeholders"]["username"] == "visitor"
                assert result["description_placeholders"]["dashboard_url"].endswith("/guest")
                assert result["description_placeholders"]["editor_url"].endswith("?dashboard=guest")
            item.async_create_entry = Mock(return_value={"type": "create_entry"})
            item.hass.config_entries.async_update_entry = Mock()
            if cls is flow.LimitedAccessDashboardOptionsFlow:
                item.hass.config_entries.async_get_known_entry = lambda _: SimpleNamespace(
                    data={"password_hash": "preserved"}, options={}
                )
            assert (await item.async_step_share({}))["type"] == "create_entry"
            if cls is flow.LimitedAccessDashboardOptionsFlow:
                assert (
                    item.async_create_entry.call_args.kwargs["data"]["password_hash"]
                    == item.config_entry.data["password_hash"]
                )


async def check_media():
    features = Feature(0)
    for feature in (
        Feature.TURN_ON,
        Feature.TURN_OFF,
        Feature.PLAY,
        Feature.PAUSE,
        Feature.NEXT_TRACK,
        Feature.PREVIOUS_TRACK,
        Feature.VOLUME_STEP,
        Feature.VOLUME_MUTE,
        Feature.VOLUME_SET,
    ):
        features |= feature
    state = State(
        "media_player.tv",
        "playing",
        {
            "supported_features": int(features),
            "volume_level": 0.3,
            "is_volume_muted": False,
            "friendly_name": "Living room TV",
        },
    )
    settings = load_settings(
        {
            "slug": "test",
            "username": "guest",
            "password_hash": "$argon2id$test",
            "entities": ["media_player.tv", "sensor.temperature"],
        }
    )
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: state),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    runtime = DashboardRuntime(hass, "test", settings, Mock())
    card = runtime.get_card("media_player.tv")
    assert card.action == "media"
    assert len(controls(state)) == 10
    assert controls(State("media_player.other", "on", {})) == []
    for control in controls(state):
        await runtime.call_card_action(
            card,
            {
                "media_action": control["action"],
                "volume": "35",
                "entity_id": "media_player.unapproved",
            },
        )
        args = hass.services.async_call.call_args.args
        assert args[0] == "media_player" and args[2]["entity_id"] == "media_player.tv"
    assert action_data(state, "volume_set", {"volume": "100"})["volume_level"] == 1
    assert action_data(state, "volume_set", {"volume": "0"})["volume_level"] == 0
    assert action_data(state, "volume_mute", {})["is_volume_muted"] is True
    for form in (
        {"media_action": "play_media"},
        {"media_action": "join"},
        *(
            {"media_action": "volume_set", "volume": value}
            for value in ("-1", "101", "nan", "inf", "bad")
        ),
    ):
        before = hass.services.async_call.call_count
        try:
            await runtime.call_card_action(card, form)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe media request accepted")
        assert hass.services.async_call.call_count == before
    try:
        await runtime.call_card_action(
            DashboardCard("media_player.unapproved", "control"), {"media_action": "turn_on"}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Unapproved entity accepted")
    for unavailable in (State("media_player.tv", "unavailable", state.attributes), None):
        try:
            action_data(unavailable, "turn_on", {})
        except ValueError:
            pass
        else:
            raise AssertionError("Unavailable entity accepted")
    html = runtime.html(
        "dashboard.html",
        account=settings.account,
        result="",
        available=True,
        reason="",
        cards=runtime.cards(),
        csrf_token="test",  # noqa: S106 - synthetic test token
        has_sprinklers=False,
    ).text
    assert 'name="media_action"' in html and "Living room TV" in html
    assert "Stop all water" not in html
    # Optional artifact for browser layout verification, never contains real credentials.
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(html, encoding="utf-8")
    manager = DashboardManager()
    manager.add(runtime)
    view = ActionView(manager)
    runtime.principal = AsyncMock(
        return_value=(settings.account, SimpleNamespace(csrf_token="csrf"))  # noqa: S106
    )
    runtime.audit = AsyncMock()
    runtime.limiter = SimpleNamespace(allow=lambda *args, **kwargs: True)
    for entity, form, expected in (
        ("media_player.tv", {"csrf_token": "wrong", "media_action": "turn_on"}, "denied"),
        ("media_player.other", {"csrf_token": "csrf", "media_action": "turn_on"}, "denied"),
        ("media_player.tv", {"csrf_token": "csrf", "media_action": "play_media"}, "denied"),
        ("media_player.tv", {"csrf_token": "csrf", "media_action": "media_pause"}, "success"),
    ):
        before = hass.services.async_call.call_count
        response = await view.post(
            SimpleNamespace(post=AsyncMock(return_value=form)), "test", entity
        )
        assert response.location.endswith(f"result={expected}")
        assert hass.services.async_call.call_count == before + (expected == "success")
    before = hass.services.async_call.call_count
    runtime.settings = replace(
        settings,
        account=replace(settings.account, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
    )
    request = SimpleNamespace(
        post=AsyncMock(return_value={"csrf_token": "csrf", "media_action": "turn_on"})
    )
    assert (await view.post(request, "test", "media_player.tv")).location.endswith("result=denied")
    runtime.settings = settings
    runtime.limiter = SimpleNamespace(allow=lambda *args, **kwargs: False)
    assert (await view.post(request, "test", "media_player.tv")).location.endswith(
        "result=rate_limited"
    )
    runtime.principal = AsyncMock(return_value=None)
    assert (await view.post(request, "test", "media_player.tv")).location.endswith("/login")
    assert hass.services.async_call.call_count == before


def _executor_hass(states=None):
    """Fake hass whose executor runs jobs inline and records what ran."""
    jobs = []

    async def run(func, *args):
        jobs.append(getattr(func, "__name__", repr(func)))
        return func(*args)

    hass = SimpleNamespace(
        async_add_executor_job=run,
        states=SimpleNamespace(get=lambda _: states),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    return hass, jobs


def _login_request(form, *, remote="198.51.100.7", csrf="login-csrf", size=200):
    return SimpleNamespace(
        content_length=size,
        cookies={LOGIN_CSRF_COOKIE: csrf},
        remote=remote,
        post=AsyncMock(return_value={"csrf_token": csrf, **form}),
    )


async def check_security():
    """Sessions end with the password or the account; login stays bounded."""
    limiter = RateLimiter(max_keys=3)
    for index in range(50):
        limiter.allow(f"key-{index}", limit=1, window_seconds=300)
    assert len(limiter) == 3, "rate limiter grew past its key cap"
    assert limiter.allow("key-49", limit=1, window_seconds=300) is False

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        password_hash = hash_password("correct horse battery")
        settings = load_settings(
            {
                "slug": "sec",
                "username": "guest",
                "password_hash": password_hash,
                "entities": ["light.porch"],
                "database_path": str(root / "sec.db"),
                "session_hours": 168,
                "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            }
        )
        hass, jobs = _executor_hass(State("light.porch", "on", {"friendly_name": "Porch"}))
        store = Store(settings.database_path)
        runtime = DashboardRuntime(hass, "sec", settings, store)
        manager = DashboardManager()
        manager.add(runtime)
        login = LoginView(manager)

        # A correct login runs Argon2 in the executor and caps the session at
        # the account's expiry, not the 168-hour session length.
        response = await login.post(
            _login_request({"username": "Guest", "password": "correct horse battery"}), "sec"
        )
        assert response.location == "/limited-access-dashboard/sec", response.location
        assert "verify_password" in jobs, "Argon2 ran on the event loop"
        token = response.cookies[SESSION_COOKIE].value
        assert int(response.cookies[SESSION_COOKIE]["max-age"]) <= 2 * 3600
        session = store.get_session(token)
        assert session.expires_at <= settings.account.expires_at
        request = SimpleNamespace(cookies={SESSION_COOKIE: token}, query={})
        assert await runtime.principal(request) is not None

        # Changing the password ends the session, even if set to the same text.
        runtime.settings = replace(
            settings,
            account=replace(settings.account, password_hash=hash_password("correct horse battery")),
        )
        assert await runtime.principal(request) is None, "old session survived a password change"

        # The account expiring ends the session too.
        runtime.settings = replace(
            settings,
            account=replace(settings.account, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        )
        assert await runtime.principal(request) is None, "session outlived the account"
        runtime.settings = settings

        # Outside the allowed hours the guest stays logged in but sees no states.
        state_view = StateView(manager)
        assert "Porch" in (await state_view.get(request, "sec")).text
        with patch.object(type(settings), "access", return_value=(False, "closed")):
            body = (await state_view.get(request, "sec")).text
            assert '"cards": []' in body and "Porch" not in body, body

        # Sessions created before credential fingerprints existed are rejected.
        legacy = root / "legacy.db"
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute(
                "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, "
                "csrf_token TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES (?, 'guest', 'c', ?, ?)",
                (
                    token_hash("legacy-token"),
                    datetime.now(UTC).isoformat(),
                    (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                ),
            )
        legacy_runtime = DashboardRuntime(
            hass, "legacy", replace(settings, database_path=legacy), Store(legacy)
        )
        legacy_request = SimpleNamespace(cookies={SESSION_COOKIE: "legacy-token"})
        assert await legacy_runtime.principal(legacy_request) is None

        # Login abuse: oversized bodies are refused unread, usernames never
        # become limiter keys, and one address gets five tries per window.
        runtime.limiter = RateLimiter()
        oversized = _login_request({"username": "guest"}, size=MAX_LOGIN_BODY_BYTES + 1)
        assert (await login.post(oversized, "sec")).location.endswith("error=invalid_request")
        oversized.post.assert_not_awaited()
        for attempt in range(LOGIN_LIMIT_PER_ADDRESS):
            response = await login.post(
                _login_request({"username": f"{'u' * 5000}{attempt}", "password": "x" * 9000}),
                "sec",
            )
            assert response.location.endswith("error=invalid_credentials"), response.location
        assert len(runtime.limiter) == 2, "login attempts created per-username limiter keys"
        blocked = await login.post(
            _login_request({"username": "guest", "password": "correct horse battery"}), "sec"
        )
        assert blocked.location.endswith("error=try_later")
        # Another address is unaffected, so one attacker cannot lock the guest out.
        other = await login.post(
            _login_request(
                {"username": "guest", "password": "correct horse battery"}, remote="203.0.113.9"
            ),
            "sec",
        )
        assert other.location == "/limited-access-dashboard/sec"
        # Audit rows are truncated, and the table is capped.
        with closing(sqlite3.connect(settings.database_path)) as connection:
            longest = connection.execute("SELECT MAX(LENGTH(username)) FROM audit_log").fetchone()
        assert longest[0] <= 64, longest
        for index in range(AUDIT_LOG_MAX_ROWS + 25):
            store.log(username="u", action_id="a", outcome="o", detail="d", remote_ip=str(index))
        with closing(sqlite3.connect(settings.database_path)) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert rows <= AUDIT_LOG_MAX_ROWS, rows

        # Removing the entry deletes its database, and nothing but *.db files.
        keep = root / "keep.txt"
        keep.write_text("not a database", encoding="utf-8")
        remove_database(keep)
        assert keep.exists()
        remove_database(settings.database_path)
        assert not settings.database_path.exists()


async def main():
    for cls in (flow.LimitedAccessDashboardConfigFlow, flow.LimitedAccessDashboardOptionsFlow):
        item = cls()
        item.handler = "test"
        saved = {"schedule": {"monday": ["09:00-17:00"]}}
        item.hass = SimpleNamespace(
            config=SimpleNamespace(time_zone="UTC"),
            config_entries=SimpleNamespace(
                async_get_known_entry=lambda _, saved=saved: SimpleNamespace(data=saved, options={})
            ),
        )
        item.async_step_share = AsyncMock(return_value={"step_id": "share"})
        result = await item.async_step_schedule()
        assert set(result["data_schema"].schema) == {"always_access"}
        result = await item.async_step_schedule({"always_access": False})
        assert result["step_id"] == "schedule_window"
        assert set(result["data_schema"].schema) == {"weekdays", "start_time", "end_time"}
        if cls is flow.LimitedAccessDashboardOptionsFlow:
            values = {
                key.schema: key.description["suggested_value"]
                for key in result["data_schema"].schema
            }
            assert values["weekdays"] == ["monday"]
            assert values["start_time"] == "09:00:00"
        result = await item.async_step_schedule_window(
            {"weekdays": [], "start_time": "09:00:00", "end_time": "17:00:00"}
        )
        assert result["errors"] == {"weekdays": "weekdays_required"}
        result = await item.async_step_schedule_window(
            {"weekdays": ["tuesday"], "start_time": "22:00:00", "end_time": "06:00:00"}
        )
        expected_step = "share" if cls is flow.LimitedAccessDashboardOptionsFlow else "appearance"
        assert result["step_id"] == expected_step
        assert item._data["schedule"] == {"tuesday": ["22:00-06:00"]}
        result = await item.async_step_schedule({"always_access": True})
        assert result["step_id"] == expected_step
        assert item._data["schedule"] == "always"
        if cls is flow.LimitedAccessDashboardConfigFlow:
            item._data["entities"] = ["light.guest", "sensor.temperature"]
            appearance = {
                "theme": "light",
                "columns": "2",
                "density": "compact",
                "grouping": "by_type",
                "accent": "#112233",
            }
            invalid = await item.async_step_appearance({**appearance, "accent": "wrong"})
            assert invalid["errors"] == {"base": "invalid_appearance"}
            assert (await item.async_step_appearance(appearance))["step_id"] == "share"
            assert item._data["initial_design"]["columns"] == 2
            assert [v["title"] for v in item._data["initial_design"]["views"]] == [
                "Lights",
                "Sensors",
            ]
            assert item._data.get("allow_guest_customization", False) is False
    await check_flows()
    await check_media()
    await check_security()
    print(
        "PASS: HA schemas, filters, retained selections, automatic zones, share links, "
        "password boundaries, media controls, HTTP CSRF/entity/action denials, "
        "session revocation, login limits, audit caps, database removal"
    )


asyncio.run(main())
