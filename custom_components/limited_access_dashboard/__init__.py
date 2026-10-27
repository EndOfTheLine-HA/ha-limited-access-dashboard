from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .config import DOMAIN, DashboardConfigError, load_settings
from .data_paths import DATA_DIRECTORY, prepare_database, remove_database
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
        database_path = await hass.async_add_executor_job(
            prepare_database,
            Path(hass.config.path(DATA_DIRECTORY)),
            entry.entry_id,
            str(raw.get("slug", entry.unique_id or "dashboard")),
            raw.get("database_path"),
        )
        raw["database_path"] = str(database_path)
        if (
            entry.data.get("database_path") != str(database_path)
            or "database_path" in entry.options
        ):
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "database_path": str(database_path)},
                options={
                    key: value for key, value in entry.options.items() if key != "database_path"
                },
            )
        settings = load_settings(raw, default_timezone=hass.config.time_zone)
        store = await hass.async_add_executor_job(Store, settings.database_path)
        runtime = await hass.async_add_executor_job(
            DashboardRuntime,
            hass,
            entry.entry_id,
            settings,
            store,
        )
    except (OSError, sqlite3.Error, DashboardConfigError, ValueError) as exc:
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
    """Delete only this entry's managed dashboard database, including layouts.

    Otherwise a dashboard re-created with the same URL name and username would
    find the old database and accept the old guest sessions again.
    """
    raw = {**entry.data, **entry.options}
    root = Path(hass.config.path(DATA_DIRECTORY))
    path = root / f"{entry.entry_id}.db"
    if not path.exists():
        path = Path(str(raw.get("database_path", root / f"{raw.get('slug', '')}.db")))
    await hass.async_add_executor_job(remove_database, path, root)
