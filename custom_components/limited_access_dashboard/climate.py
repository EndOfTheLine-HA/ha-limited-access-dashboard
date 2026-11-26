"""Validate thermostat settings for both native and custom guest cards."""

import math

# Home Assistant ClimateEntityFeature.TARGET_TEMPERATURE and
# TARGET_TEMPERATURE_RANGE bit values. Kept independent of HA for unit tests.
TARGET_TEMPERATURE = 1
TARGET_TEMPERATURE_RANGE = 2
FAN_MODE = 8
TURN_OFF = 128
TURN_ON = 256
HVAC_MODES = {"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}
SINGLE = ("temperature",)
RANGE = ("target_temp_low", "target_temp_high")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Temperature must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Temperature must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError("Temperature must be a finite number")
    return result


def controls(state):
    if state is None or state.state in {"unknown", "unavailable"}:
        return None
    attrs = state.attributes
    try:
        minimum = number(attrs.get("min_temp"))
        maximum = number(attrs.get("max_temp"))
        features = int(attrs.get("supported_features", 0))
    except (ValueError, TypeError, OverflowError):
        return None
    if minimum > maximum:
        return None
    if state.state == "heat_cool":
        fields = RANGE if features & TARGET_TEMPERATURE_RANGE else ()
    elif features & TARGET_TEMPERATURE:
        fields = SINGLE
    else:
        fields = ()
    if not fields:
        return None
    return {
        "fields": list(fields),
        "min": minimum,
        "max": maximum,
        "values": {field: attrs.get(field) for field in fields},
    }



def options(value):
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def operation_controls(state):
    """Expose only supported modes and power operations for this entity."""
    if state is None or state.state in {"unknown", "unavailable"}:
        return {"hvac_modes": [], "fan_modes": [], "hvac_mode": None,
                "fan_mode": None, "turn_on": False, "turn_off": False}
    attrs = state.attributes
    try:
        features = int(attrs.get("supported_features", 0))
    except (ValueError, TypeError, OverflowError):
        features = 0
    if features < 0:
        features = 0
    return {
        "hvac_modes": [mode for mode in options(attrs.get("hvac_modes")) if mode in HVAC_MODES],
        "fan_modes": options(attrs.get("fan_modes")) if features & FAN_MODE else [],
        "hvac_mode": state.state,
        "fan_mode": attrs.get("fan_mode"),
        "turn_on": bool(features & TURN_ON),
        "turn_off": bool(features & TURN_OFF),
    }


def form_action(form):
    """Legacy temperature forms remain valid; no arbitrary fields are forwarded."""
    if hasattr(form, "getall") and any(len(form.getall(key)) != 1 for key in form):
        raise ValueError("Duplicate thermostat parameters")
    return form.get("climate_action", "set_temperature"), {
        key: value for key, value in form.items() if key not in {"csrf_token", "climate_action"}
    }


def action_data(state, service, data):
    if state is None or state.state in {"unknown", "unavailable"}:
        raise ValueError("Thermostat is unavailable")
    if service in ("set_hvac_mode", "set_fan_mode"):
        field = "hvac_mode" if service == "set_hvac_mode" else "fan_mode"
        allowed = operation_controls(state)[field + "s"]
        if set(data) != {field} or not isinstance(data[field], str) or data[field] not in allowed:
            raise ValueError("Thermostat mode is not supported or parameters are invalid")
        return {"entity_id": state.entity_id, field: data[field]}
    if service in ("turn_on", "turn_off"):
        if data or not operation_controls(state)[service]:
            raise ValueError("Thermostat power action is not supported or parameters are invalid")
        return {"entity_id": state.entity_id}
    if service != "set_temperature":
        raise ValueError("Thermostat action is not permitted")
    control = controls(state)
    if control is None:
        raise ValueError("Thermostat temperature control is unavailable")
    fields = control["fields"]
    if set(data) != set(fields):
        raise ValueError("Unsupported thermostat parameters or setpoint type")
    clean = {field: number(data[field]) for field in fields}
    if any(not control["min"] <= value <= control["max"] for value in clean.values()):
        raise ValueError("Temperature is outside the thermostat's permitted range")
    if fields == list(RANGE) and clean[RANGE[0]] > clean[RANGE[1]]:
        raise ValueError("Heating setpoint must not exceed cooling setpoint")
    return {"entity_id": state.entity_id, **clean}
