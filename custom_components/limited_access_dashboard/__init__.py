from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .config import DEFAULT_DATA_DIR, DOMAIN, DashboardConfigError, load_settings
from .editor import register_editor
from .entities import is_sprinkler
from .http import DashboardManager, DashboardRuntime, register_views
from .links import dashboard_url
from .storage import Store

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): dict}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    manager = DashboardManager()
    hass.data[DOMAIN] = manager
    register_views(hass, manager)
    register_editor(hass, manager)

    raw: Any = config.get(DOMAIN)
    if isinstance(raw, dict):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=dict(raw),
            ),
            "Import Limited Access Dashboard YAML configuration",
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    raw = {**entry.data, **entry.options}
    raw["sprinkler_entities"] = list(
        dict.fromkeys(
            [
                *raw.get("sprinkler_entities", []),
                *(entity for entity in raw.get("entities", []) if is_sprinkler(hass, entity)),
            ]
        )
    )
    try:
        settings = load_settings(raw, default_timezone=hass.config.time_zone)
        store = await hass.async_add_executor_job(Store, settings.database_path)
        runtime = await hass.async_add_executor_job(
            DashboardRuntime,
            hass,
            entry.entry_id,
            settings,
            store,
        )
    except (OSError, DashboardConfigError, ValueError) as exc:
        _LOGGER.error("Unable to initialize Limited Access Dashboard: %s", exc)
        return False

    manager: DashboardManager = hass.data[DOMAIN]
    manager.add(runtime)
    entry.runtime_data = runtime
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=settings.title,
        manufacturer="Limited Access Dashboard",
        model="Guest dashboard",
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=(
            url if (url := dashboard_url(hass, settings.slug)).startswith("http") else None
        ),
    )
    _LOGGER.info("Limited Access Dashboard registered at %s", settings.base_path)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager: DashboardManager = hass.data[DOMAIN]
    manager.remove(entry.entry_id)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the dashboard's session and audit database with the entry.

    Otherwise a dashboard re-created with the same URL name and username would
    find the old database and accept the old guest sessions again.
    """
    raw = {**entry.data, **entry.options}
    slug = str(raw.get("slug", ""))
    path = Path(str(raw.get("database_path", f"{DEFAULT_DATA_DIR}/{slug}.db")))
    await hass.async_add_executor_job(remove_database, path)


def remove_database(path: Path) -> None:
    """Remove an SQLite database and its side files. Only ever touches *.db."""
    if path.suffix != ".db":
        _LOGGER.warning("Not removing %s: not an SQLite .db file", path.name)
        return
    for candidate in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            _LOGGER.warning("Could not remove %s: %s", candidate.name, exc)
