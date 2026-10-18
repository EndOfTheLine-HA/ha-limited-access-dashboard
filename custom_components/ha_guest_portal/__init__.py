from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .config import DOMAIN, PortalConfigError, load_settings
from .http import PortalRuntime, register_views
from .storage import Store

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    raw: Any = config.get(DOMAIN)
    if not isinstance(raw, dict):
        _LOGGER.error("%s configuration must be a mapping", DOMAIN)
        return False
    try:
        settings = load_settings(dict(raw))
        store = await hass.async_add_executor_job(Store, settings.database_path)
        runtime = await hass.async_add_executor_job(PortalRuntime, hass, settings, store)
    except (OSError, PortalConfigError, ValueError) as exc:
        _LOGGER.error("Unable to initialize HA Guest Portal: %s", exc)
        return False

    register_views(hass, runtime)
    hass.data[DOMAIN] = runtime
    _LOGGER.info("HA Guest Portal registered at %s", settings.base_path)
    return True
