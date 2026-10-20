"""Entity classification from HA metadata, never from display names."""

from homeassistant.helpers import entity_registry as er


def is_sprinkler(hass, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    return bool(
        entity_id.startswith("switch.")
        and entry
        and entry.platform == "rachio"
        and "-zone-" in entry.unique_id
    )


def is_unsupported_irrigation(hass, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    return bool(
        entity_id.startswith("switch.")
        and entry
        and entry.platform == "rachio"
        and not is_sprinkler(hass, entity_id)
    )
