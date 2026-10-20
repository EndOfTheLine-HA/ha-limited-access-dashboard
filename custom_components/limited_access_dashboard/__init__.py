from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .config import DOMAIN, DashboardConfigError, load_settings
from .http import DashboardManager, DashboardRuntime, register_views
from .storage import Store

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): dict}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    manager = DashboardManager()
    hass.data[DOMAIN] = manager
    register_views(hass, manager)

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
    _LOGGER.info("Limited Access Dashboard registered at %s", settings.base_path)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager: DashboardManager = hass.data[DOMAIN]
    manager.remove(entry.entry_id)
    return True
