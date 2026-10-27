"""Presentation validation does not depend on a running Home Assistant."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "lad_design", Path(__file__).parents[1] / "custom_components/limited_access_dashboard/design.py"
)
design = importlib.util.module_from_spec(spec)
spec.loader.exec_module(design)
ENTITIES = ["light.guest", "sensor.temperature"]


def test_tabs_sections_colors_and_duplicate_entity_placements():
    raw = design.default_design(ENTITIES)
    raw["views"].append(
        {
            "title": "Lights",
            "sections": [
                {
                    "title": "Bedroom",
                    "cards": [
                        {
                            "type": "entity",
                            "entity": "light.guest",
                            "color": "#112233",
                            "wide": True,
                        }
                    ],
                }
            ],
        }
    )
    result = design.validate_design(raw, ENTITIES)
    assert result["views"][1]["sections"][0]["cards"][0]["wide"]
    assert result["views"][1]["sections"][0]["cards"][0]["color"] == "#112233"


@pytest.mark.parametrize(
    "resource",
    [
        "https://example.com/card.js",
        "//example.com/card.js",
        "/hacsfiles/../secrets.js",
        "/local/%2e%2e/secrets.js",
        "/local/card.html",
        "/local/card.js#fragment",
        "/local/..\\secrets.js",
        "/local/card\n.js",
        "/local/card.js?token=\tvalue",
    ],
)
def test_rejects_external_or_traversing_resources(resource):
    with pytest.raises(ValueError):
        design.validate_resources([resource])


def test_local_resource_queries_are_preserved():
    assert design.validate_resources(["/hacsfiles/test/card.js?v=3"]) == [
        "/hacsfiles/test/card.js?v=3"
    ]


def test_guests_can_rearrange_but_cannot_add_custom_code_or_entities():
    owner = design.default_design(ENTITIES)
    custom = {
        "type": "lovelace",
        "config": {
            "type": "custom:example-card",
            "cards": [{"type": "entity", "entity": "sensor.temperature"}],
        },
    }
    owner["views"][0]["sections"][0]["cards"].append(custom)
    guest = deepcopy(owner)
    guest["views"][0]["sections"][0]["cards"].reverse()
    assert design.validate_design(guest, ENTITIES, approved=owner)
    guest["views"][0]["sections"][0]["cards"][0]["config"]["type"] = "custom:other-card"
    with pytest.raises(ValueError, match="Only the owner"):
        design.validate_design(guest, ENTITIES, approved=owner)
    custom["config"]["cards"][0]["entity"] = "light.private"
    with pytest.raises(ValueError, match="not approved"):
        design.validate_design(owner, ENTITIES)


@pytest.mark.parametrize(
    "key,value",
    [
        ("columns", 99),
        ("columns", True),
        ("accent", "red; background:url(https://example.com)"),
        ("views", []),
    ],
)
def test_rejects_invalid_layout(key, value):
    raw = design.default_design(ENTITIES)
    raw[key] = value
    with pytest.raises(ValueError):
        design.validate_design(raw, ENTITIES)


def test_entity_removal_preserves_guest_appearance_and_unaffected_custom_cards():
    owner = design.default_design(ENTITIES)
    owner["views"][0]["sections"][0]["cards"].extend(
        [
            {"type": "lovelace", "config": {"type": "custom:keep", "entity": "light.guest"}},
            {
                "type": "lovelace",
                "config": {"type": "custom:removed", "entity": "sensor.temperature"},
            },
        ]
    )
    guest = deepcopy(owner)
    guest["views"][0]["title"] = "My favorites"
    guest["accent"] = "#123456"
    entities = ["light.guest", "switch.new"]
    cleaned_owner = design.reconcile_design(owner, entities)
    cleaned_guest = design.reconcile_design(guest, entities, approved=cleaned_owner)
    assert cleaned_guest["accent"] == "#123456"
    assert cleaned_guest["views"][0]["title"] == "My favorites"
    cards = cleaned_guest["views"][0]["sections"][0]["cards"]
    assert len(cards) == 2
    assert cards[1]["config"]["type"] == "custom:keep"
    assert not any(card.get("entity") == "switch.new" for card in cards)
