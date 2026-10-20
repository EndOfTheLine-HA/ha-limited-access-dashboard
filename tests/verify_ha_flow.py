"""Run with HA's Python: python tests/verify_ha_flow.py REPOSITORY_ROOT."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, sys.argv[1])
from custom_components.limited_access_dashboard import config_flow as flow  # noqa: E402
from custom_components.limited_access_dashboard.security import (  # noqa: E402
    hash_password,
    verify_password,
)


async def main():
    assert verify_password(hash_password("eight888"), "eight888")
    try:
        hash_password("seven77")
    except ValueError:
        pass
    else:
        raise AssertionError("Seven-character password accepted")
    schema = flow._cards_schema({}).schema
    assert "sprinkler_entities" not in schema and "duration_minutes" not in schema
    assert "enable_sprinklers" in schema
    for cls in (flow.LimitedAccessDashboardConfigFlow, flow.LimitedAccessDashboardOptionsFlow):
        item = cls()
        if cls is flow.LimitedAccessDashboardOptionsFlow:
            item.handler = "test"
            item.hass = SimpleNamespace(
                config_entries=SimpleNamespace(
                    async_get_entry=lambda _: SimpleNamespace(
                        data={"password_hash": hash_password("eight888")}, options={}
                    )
                )
            )
        item.async_step_schedule = AsyncMock(return_value={"step_id": "schedule"})
        result = await item.async_step_cards(
            {"entities": ["light.porch"], "enable_sprinklers": False}
        )
        assert result["step_id"] == "schedule"
        assert item._data["sprinkler_entities"] == []
        result = await item.async_step_cards({"entities": [], "enable_sprinklers": True})
        assert result["step_id"] == "sprinklers"
        result = await item.async_step_sprinklers({"sprinkler_entities": []})
        assert result["errors"]
        result = await item.async_step_sprinklers(
            {"sprinkler_entities": ["switch.zone"], "duration_minutes": 5}
        )
        assert result["step_id"] == "schedule"
    print("HA flow checks passed: generic, opt-in sprinklers, empty zones, password boundaries")


asyncio.run(main())
