"""Run with HA's Python: python tests/verify_ha_flow.py REPOSITORY_ROOT.

Uses fake states/services: never operates real devices or edits HA configuration.
"""

import asyncio
import sys
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
from custom_components.limited_access_dashboard.config import (  # noqa: E402
    DashboardCard,
    load_settings,
)
from custom_components.limited_access_dashboard.http import (  # noqa: E402
    ActionView,
    DashboardManager,
    DashboardRuntime,
)
from custom_components.limited_access_dashboard.links import dashboard_url  # noqa: E402
from custom_components.limited_access_dashboard.media import action_data, controls  # noqa: E402
from custom_components.limited_access_dashboard.security import (  # noqa: E402
    hash_password,
    verify_password,
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
    assert verify_password(hash_password("eight888"), "eight888")
    try:
        hash_password("seven77")
    except ValueError:
        pass
    else:
        raise AssertionError("Seven-character password accepted")
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


async def main():
    await check_flows()
    await check_media()
    print(
        "PASS: HA schemas, filters, retained selections, automatic zones, share links, "
        "password boundaries, media controls, HTTP CSRF/entity/action denials"
    )


asyncio.run(main())
