"""Presentation documents. Entity permissions always come from Settings.cards."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import unquote, urlsplit

MAX_DOCUMENT_BYTES = 131072


def default_design(entities: list[str]) -> dict[str, Any]:
    return {
        "theme": "dark",
        "accent": "#03a9f4",
        "background": "#11131a",
        "card_background": "#1c1f2a",
        "columns": 3,
        "density": "comfortable",
        "views": [
            {
                "title": "Home",
                "sections": [
                    {
                        "title": "",
                        "cards": [{"type": "entity", "entity": entity} for entity in entities],
                    }
                ],
            }
        ],
    }


def setup_design(entities: list[str], values: dict) -> dict:
    result = default_design(entities)
    result.update({key: values[key] for key in ("theme", "columns", "density", "accent")})
    if values["theme"] == "light":
        result.update(background="#f3f5f7", card_background="#ffffff")
    if values["grouping"] == "by_type":
        labels = {
            "light": "Lights",
            "switch": "Plugs & switches",
            "media_player": "Media",
            "sensor": "Sensors",
            "binary_sensor": "Sensors",
            "climate": "Climate",
        }
        groups: dict[str, list] = {}
        for entity in entities:
            groups.setdefault(labels.get(entity.split(".")[0], "Other"), []).append(
                {"type": "entity", "entity": entity}
            )
        result["views"] = [
            {"title": title, "sections": [{"title": "", "cards": cards}]}
            for title, cards in groups.items()
        ]
    return validate_design(result, entities)


def validate_resources(resources: Any) -> list[str]:
    if not isinstance(resources, list) or len(resources) > 30:
        raise ValueError("Use at most 30 local JavaScript module resources")
    result = []
    for resource in resources:
        if not isinstance(resource, str) or len(resource) > 512:
            raise ValueError("Invalid resource URL")
        parsed = urlsplit(resource)
        decoded = unquote(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or not decoded.startswith(("/hacsfiles/", "/local/"))
            or "\\" in decoded
            or any(part in {".", ".."} for part in decoded.split("/"))
            or not decoded.endswith(".js")
        ):
            raise ValueError("Resources must be local /hacsfiles/ or /local/ .js modules")
        result.append(resource)
    return list(dict.fromkeys(result))


def _text(value: Any, maximum: int = 100) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("Invalid text or text is too long")
    return value


def _color(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Colors must be six-digit hex values")
    return value


def _entity_refs(value: Any, allowed: set[str], key: str = "") -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            _entity_refs(child, allowed, name)
    elif isinstance(value, list):
        for child in value:
            _entity_refs(child, allowed, "entity" if key == "entities" else key)
    elif key in {"entity", "entity_id", "entities"} and isinstance(value, str):
        if value not in allowed:
            raise ValueError(f"Entity is not approved: {value}")


def validate_design(raw: Any, entities: list[str], *, approved: dict | None = None) -> dict:
    """Validate owner designs, or guest presentation changes against the owner design."""
    if not isinstance(raw, dict) or len(json.dumps(raw, allow_nan=False)) > MAX_DOCUMENT_BYTES:
        raise ValueError("Invalid or oversized dashboard design")
    allowed = set(entities)
    result = default_design(entities)
    for key in ("accent", "background", "card_background"):
        result[key] = _color(raw.get(key, result[key]))
    for key, choices in (("theme", {"dark", "light"}), ("density", {"comfortable", "compact"})):
        value = raw.get(key, result[key])
        if value not in choices:
            raise ValueError(f"Invalid {key}")
        result[key] = value
    columns = raw.get("columns", 3)
    if type(columns) is not int or not 1 <= columns <= 4:
        raise ValueError("Columns must be between 1 and 4")
    result["columns"] = columns
    views = raw.get("views")
    if not isinstance(views, list) or not 1 <= len(views) <= 12:
        raise ValueError("Use between 1 and 12 dashboard tabs")
    approved_configs = {
        json.dumps(card.get("config"), sort_keys=True)
        for view in (approved or {}).get("views", [])
        for section in view["sections"]
        for card in section["cards"]
        if card["type"] == "lovelace"
    }
    result["views"] = []
    total = 0
    for view in views:
        sections = view.get("sections") if isinstance(view, dict) else None
        if not isinstance(sections, list) or not 1 <= len(sections) <= 20:
            raise ValueError("Each tab needs between 1 and 20 sections")
        clean_view = {"title": _text(view.get("title", "Tab")), "sections": []}
        for section in sections:
            cards = section.get("cards") if isinstance(section, dict) else None
            if not isinstance(cards, list):
                raise ValueError("Section cards must be a list")
            clean_section = {"title": _text(section.get("title", "")), "cards": []}
            for card in cards:
                total += 1
                if total > 200 or not isinstance(card, dict):
                    raise ValueError("Use at most 200 cards")
                kind = card.get("type", "entity")
                clean = {
                    "type": kind,
                    "title": _text(card.get("title", "")),
                    "color": _color(card.get("color", result["accent"])),
                    "wide": bool(card.get("wide", False)),
                }
                if kind == "entity":
                    if card.get("entity") not in allowed:
                        raise ValueError("Card entity is not approved")
                    clean["entity"] = card["entity"]
                    clean["display"] = card.get("display", "tile")
                    if clean["display"] not in {"tile", "compact", "state"}:
                        raise ValueError("Invalid card display")
                elif kind == "lovelace":
                    config = card.get("config")
                    if not isinstance(config, dict) or not isinstance(config.get("type"), str):
                        raise ValueError("A Lovelace card needs a configuration with a type")
                    _entity_refs(config, allowed)
                    if (
                        approved is not None
                        and json.dumps(config, sort_keys=True) not in approved_configs
                    ):
                        raise ValueError("Only the owner can add or change Lovelace configurations")
                    clean["config"] = deepcopy(config)
                else:
                    raise ValueError("Unsupported card type")
                clean_section["cards"].append(clean)
            clean_view["sections"].append(clean_section)
        result["views"].append(clean_view)
    return result
