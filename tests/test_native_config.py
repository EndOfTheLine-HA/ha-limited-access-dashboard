from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType


def _load_config_module() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "custom_components" / "limited_access_dashboard" / "config.py"
    )
    spec = importlib.util.spec_from_file_location("limited_access_dashboard_native_config", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


config = _load_config_module()


def test_flat_config_builds_restricted_cards() -> None:
    settings = config.load_settings(
        {
            "title": "Guest controls",
            "slug": "Rental Guest",
            "timezone": "UTC",
            "username": "guest",
            "display_name": "Welcome",
            "password_hash": "$argon2id$test",
            "entities": ["light.porch", "sensor.outdoor_temperature"],
            "sprinkler_entities": ["switch.front_lawn"],
            "schedule": "always",
        }
    )

    assert settings.slug == "rental-guest"
    assert settings.base_path == "/limited-access-dashboard/rental-guest"
    assert [card.action for card in settings.cards] == ["toggle", None, "sprinkler"]
    assert settings.access(datetime.now(UTC)) == (True, "")


def test_legacy_yaml_converts_to_visual_entry() -> None:
    converted = config.legacy_to_entry(
        {
            "title": "Sprinklers",
            "timezone": "UTC",
            "account": {
                "username": "landscaper",
                "display_name": "Landscaper",
                "password_hash": "$argon2id$test",
            },
            "zones": {
                "front": {"name": "Front", "entity_id": "switch.front_lawn"},
            },
            "schedule": {"monday": ["08:00-12:00"]},
        },
        default_timezone="UTC",
    )

    assert converted["slug"] == "landscaper"
    assert converted["sprinkler_entities"] == ["switch.front_lawn"]
    assert converted["schedule"] == {"monday": ["08:00-12:00"]}
