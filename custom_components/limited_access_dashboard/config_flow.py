from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .config import (
    DAYS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_SESSION_HOURS,
    DOMAIN,
    SUPPORTED_DOMAINS,
    legacy_to_entry,
    normalize_slug,
)
from .entities import is_sprinkler, is_unsupported_irrigation
from .links import dashboard_url
from .security import hash_password

CONF_TITLE = "title"
CONF_SLUG = "slug"
CONF_USERNAME = "username"
CONF_DISPLAY_NAME = "display_name"
CONF_PASSWORD = "password"  # noqa: S105
CONF_PASSWORD_HASH = "password_hash"  # noqa: S105
CONF_SESSION_HOURS = "session_hours"
CONF_ACTIVE_FROM = "active_from"
CONF_EXPIRES_AT = "expires_at"
CONF_ENTITIES = "entities"
CONF_SPRINKLER_ENTITIES = "sprinkler_entities"
CONF_DURATION_MINUTES = "duration_minutes"
CONF_ALWAYS_ACCESS = "always_access"
CONF_WEEKDAYS = "weekdays"
CONF_START_TIME = "start_time"
CONF_END_TIME = "end_time"
CONF_SCHEDULE = "schedule"


def _suggested(key: str, current: dict[str, Any], fallback: Any) -> vol.Marker:
    return vol.Required(key, description={"suggested_value": current.get(key, fallback)})


def _dashboard_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _suggested(CONF_TITLE, current, "Limited Access Dashboard"): selector.TextSelector(),
            _suggested(CONF_SLUG, current, "guest"): selector.TextSelector(
                selector.TextSelectorConfig(autocomplete="off")
            ),
        }
    )


def _account_schema(current: dict[str, Any], *, password_required: bool) -> vol.Schema:
    password_key: vol.Marker
    if password_required:
        password_key = vol.Required(CONF_PASSWORD)
    else:
        password_key = vol.Optional(CONF_PASSWORD)
    schema: dict[vol.Marker, Any] = {
        _suggested(CONF_USERNAME, current, "guest"): selector.TextSelector(
            selector.TextSelectorConfig(autocomplete="username")
        ),
        _suggested(CONF_DISPLAY_NAME, current, "Guest"): selector.TextSelector(),
        password_key: selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="new-password",
            )
        ),
        _suggested(CONF_SESSION_HOURS, current, DEFAULT_SESSION_HOURS): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=168,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="hours",
            )
        ),
        vol.Optional(
            CONF_ACTIVE_FROM,
            description={"suggested_value": current.get(CONF_ACTIVE_FROM)},
        ): selector.DateTimeSelector(),
        vol.Optional(
            CONF_EXPIRES_AT,
            description={"suggested_value": current.get(CONF_EXPIRES_AT)},
        ): selector.DateTimeSelector(),
    }
    return vol.Schema(schema)


ENTITY_TYPES = {
    "all": "All supported entities",
    "light": "Lights",
    "switch": "Switches",
    "outlet": "Plugs / outlets",
    "media_player": "Media players",
    "sensor": "Sensors",
    "binary_sensor": "Binary sensors",
    "climate": "Climate (view only)",
    "button": "Buttons",
    "input_boolean": "Toggle helpers",
    "scene": "Scenes",
    "script": "Scripts",
}


def _cards_schema(current: dict[str, Any], entity_type: str = "all") -> vol.Schema:
    entity_filter = {"domain": list(SUPPORTED_DOMAINS)}
    if entity_type == "outlet":
        entity_filter = {"domain": "switch", "device_class": "outlet"}
    elif entity_type != "all":
        entity_filter = {"domain": entity_type}
    selected = list(
        dict.fromkeys([*current.get(CONF_ENTITIES, []), *current.get(CONF_SPRINKLER_ENTITIES, [])])
    )
    fields = {}
    if selected:
        fields[vol.Optional(CONF_ENTITIES, description={"suggested_value": selected})] = (
            selector.EntitySelector(
                selector.EntitySelectorConfig(
                    include_entities=selected, multiple=True, reorder=True
                )
            )
        )
    fields.update(
        {
            vol.Optional("add_entities"): selector.EntitySelector(
                selector.EntitySelectorConfig(filter=entity_filter, multiple=True)
            ),
            vol.Required("choose_another_type", default=False): selector.BooleanSelector(),
        }
    )
    return vol.Schema(fields)


def _sprinklers_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _suggested(
                CONF_DURATION_MINUTES, current, DEFAULT_DURATION_MINUTES
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=60,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                    unit_of_measurement="minutes",
                )
            ),
        }
    )


def _schedule_fields(current: dict[str, Any]) -> dict[str, Any]:
    schedule = current.get(CONF_SCHEDULE, "always")
    if schedule == "always" or not isinstance(schedule, dict):
        return {
            CONF_ALWAYS_ACCESS: True,
            CONF_WEEKDAYS: list(DAYS),
            CONF_START_TIME: "08:00:00",
            CONF_END_TIME: "18:00:00",
        }
    weekdays = [day for day in DAYS if day in schedule]
    first_window = next(
        (ranges[0] for ranges in schedule.values() if isinstance(ranges, list) and ranges),
        "08:00-18:00",
    )
    start, end = str(first_window).split("-", maxsplit=1)
    return {
        CONF_ALWAYS_ACCESS: False,
        CONF_WEEKDAYS: weekdays,
        CONF_START_TIME: f"{start}:00" if len(start) == 5 else start,
        CONF_END_TIME: f"{end}:00" if len(end) == 5 else end,
    }


def _schedule_schema(current: dict[str, Any]) -> vol.Schema:
    values = _schedule_fields(current)
    return vol.Schema(
        {
            _suggested(CONF_ALWAYS_ACCESS, values, True): selector.BooleanSelector(),
            _suggested(CONF_WEEKDAYS, values, list(DAYS)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(DAYS),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            _suggested(CONF_START_TIME, values, "08:00:00"): selector.TimeSelector(),
            _suggested(CONF_END_TIME, values, "18:00:00"): selector.TimeSelector(),
        }
    )


def _normalize_datetime_values(data: dict[str, Any]) -> None:
    for key in (CONF_ACTIVE_FROM, CONF_EXPIRES_AT):
        value = data.get(key)
        if isinstance(value, datetime):
            data[key] = value.isoformat()
        elif not value:
            data.pop(key, None)


def _build_schedule(user_input: dict[str, Any]) -> str | dict[str, list[str]]:
    if user_input[CONF_ALWAYS_ACCESS]:
        return "always"
    start = str(user_input[CONF_START_TIME])[:5]
    end = str(user_input[CONF_END_TIME])[:5]
    return {day: [f"{start}-{end}"] for day in user_input.get(CONF_WEEKDAYS, [])}


class SprinklerStep:
    async def async_step_entity_filter(self, user_input=None):
        if user_input is not None:
            self._entity_type = user_input["entity_type"]
            return await self.async_step_cards()
        return self.async_show_form(
            step_id="entity_filter",
            data_schema=vol.Schema(
                {
                    vol.Required("entity_type", default="all"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": key, "label": label}
                                for key, label in ENTITY_TYPES.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_cards(self, user_input=None):
        current = getattr(self, "_current", self._data)
        errors = {}
        if user_input is not None:
            entities = list(
                dict.fromkeys(
                    [*user_input.get(CONF_ENTITIES, []), *user_input.get("add_entities", [])]
                )
            )
            if any(entity.split(".")[0] not in SUPPORTED_DOMAINS for entity in entities):
                errors[CONF_ENTITIES] = "unsupported_entity"
            elif any(is_unsupported_irrigation(self.hass, entity) for entity in entities):
                errors[CONF_ENTITIES] = "unsupported_irrigation"
            elif not entities and not user_input.get("choose_another_type"):
                errors[CONF_ENTITIES] = "entities_required"
            else:
                self._data[CONF_ENTITIES] = entities
                self._data[CONF_SPRINKLER_ENTITIES] = [
                    entity
                    for entity in entities
                    if is_sprinkler(self.hass, entity)
                    or entity in current.get(CONF_SPRINKLER_ENTITIES, [])
                ]
                if user_input.get("choose_another_type"):
                    return await self.async_step_entity_filter()
                if self._data[CONF_SPRINKLER_ENTITIES]:
                    return await self.async_step_sprinklers()
                return await self.async_step_schedule()
            current = {**current, CONF_ENTITIES: entities}
        return self.async_show_form(
            step_id="cards",
            data_schema=_cards_schema(current, getattr(self, "_entity_type", "all")),
            errors=errors,
            description_placeholders={
                "entity_type": ENTITY_TYPES[getattr(self, "_entity_type", "all")]
            },
        )

    async def async_step_sprinklers(self, user_input=None):
        current = getattr(self, "_current", self._data)
        if user_input is not None:
            self._data[CONF_DURATION_MINUTES] = user_input[CONF_DURATION_MINUTES]
            return await self.async_step_schedule()
        return self.async_show_form(step_id="sprinklers", data_schema=_sprinklers_schema(current))

    async def async_step_share(self, user_input=None):
        current = getattr(self, "_current", self._data)
        if user_input is not None:
            if isinstance(self, config_entries.OptionsFlow):
                merged = deepcopy(current)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, title=merged[CONF_TITLE], unique_id=merged[CONF_SLUG]
                )
                return self.async_create_entry(title="", data=merged)
            return self.async_create_entry(title=self._data[CONF_TITLE], data=self._data)
        return self.async_show_form(
            step_id="share",
            data_schema=vol.Schema({}),
            description_placeholders={
                "dashboard_url": dashboard_url(self.hass, current[CONF_SLUG]),
                "username": current[CONF_USERNAME],
            },
        )


class LimitedAccessDashboardConfigFlow(SprinklerStep, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LimitedAccessDashboardOptionsFlow:
        return LimitedAccessDashboardOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = normalize_slug(user_input[CONF_SLUG])
            if not slug:
                errors[CONF_SLUG] = "invalid_slug"
            else:
                await self.async_set_unique_id(slug)
                self._abort_if_unique_id_configured()
                self._data.update(user_input)
                self._data[CONF_SLUG] = slug
                return await self.async_step_account()
        return self.async_show_form(
            step_id="user",
            data_schema=_dashboard_schema(self._data),
            errors=errors,
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip().lower()
            password = str(user_input[CONF_PASSWORD])
            if len(username) < 3:
                errors[CONF_USERNAME] = "invalid_username"
            elif len(password) < 8:
                errors[CONF_PASSWORD] = "password_too_short"
            else:
                normalized = dict(user_input)
                normalized[CONF_USERNAME] = username
                _normalize_datetime_values(normalized)
                normalized[CONF_PASSWORD_HASH] = await self.hass.async_add_executor_job(
                    hash_password, password
                )
                normalized.pop(CONF_PASSWORD, None)
                self._data.update(normalized)
                return await self.async_step_entity_filter()
        return self.async_show_form(
            step_id="account",
            data_schema=_account_schema(self._data, password_required=True),
            errors=errors,
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_ALWAYS_ACCESS] and not user_input.get(CONF_WEEKDAYS):
                errors[CONF_WEEKDAYS] = "weekdays_required"
            else:
                self._data[CONF_SCHEDULE] = _build_schedule(user_input)
                self._data["timezone"] = self.hass.config.time_zone
                return await self.async_step_share()
        return self.async_show_form(
            step_id="schedule",
            data_schema=_schedule_schema(self._data),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        try:
            data = legacy_to_entry(user_input, default_timezone=self.hass.config.time_zone)
        except (TypeError, ValueError):
            return self.async_abort(reason="invalid_import")
        await self.async_set_unique_id(data[CONF_SLUG])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=data[CONF_TITLE], data=data)


class LimitedAccessDashboardOptionsFlow(SprinklerStep, config_entries.OptionsFlowWithReload):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options, **self._data}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            slug = normalize_slug(user_input[CONF_SLUG])
            duplicate = any(
                entry.entry_id != self.config_entry.entry_id and entry.unique_id == slug
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            )
            if not slug:
                errors[CONF_SLUG] = "invalid_slug"
            elif duplicate:
                errors[CONF_SLUG] = "slug_in_use"
            else:
                self._data.update(user_input)
                self._data[CONF_SLUG] = slug
                return await self.async_step_account()
        return self.async_show_form(
            step_id="init",
            data_schema=_dashboard_schema(self._current),
            errors=errors,
            description_placeholders={
                "dashboard_url": dashboard_url(self.hass, self._current[CONF_SLUG])
            },
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized = dict(user_input)
            username = str(normalized[CONF_USERNAME]).strip().lower()
            password = str(normalized.pop(CONF_PASSWORD, ""))
            if len(username) < 3:
                errors[CONF_USERNAME] = "invalid_username"
            elif password and len(password) < 8:
                errors[CONF_PASSWORD] = "password_too_short"
            else:
                normalized[CONF_USERNAME] = username
                _normalize_datetime_values(normalized)
                if password:
                    normalized[CONF_PASSWORD_HASH] = await self.hass.async_add_executor_job(
                        hash_password, password
                    )
                else:
                    normalized[CONF_PASSWORD_HASH] = self._current[CONF_PASSWORD_HASH]
                self._data.update(normalized)
                return await self.async_step_entity_filter()
        return self.async_show_form(
            step_id="account",
            data_schema=_account_schema(self._current, password_required=False),
            errors=errors,
            description_placeholders={"password_note": "Leave blank to keep the current password."},
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_ALWAYS_ACCESS] and not user_input.get(CONF_WEEKDAYS):
                errors[CONF_WEEKDAYS] = "weekdays_required"
            else:
                self._data[CONF_SCHEDULE] = _build_schedule(user_input)
                return await self.async_step_share()
        return self.async_show_form(
            step_id="schedule",
            data_schema=_schedule_schema(self._current),
            errors=errors,
        )
