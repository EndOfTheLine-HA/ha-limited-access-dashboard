"""Small, explicit media action allowlist shared by rendering and execution."""

import math

from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature

FEATURES = {
    "turn_on": (Feature.TURN_ON, "Power on"),
    "turn_off": (Feature.TURN_OFF, "Power off"),
    "media_play": (Feature.PLAY, "Play"),
    "media_pause": (Feature.PAUSE, "Pause"),
    "media_previous_track": (Feature.PREVIOUS_TRACK, "Previous"),
    "media_next_track": (Feature.NEXT_TRACK, "Next"),
    "volume_down": (Feature.VOLUME_STEP, "Volume −"),
    "volume_up": (Feature.VOLUME_STEP, "Volume +"),
    "volume_mute": (Feature.VOLUME_MUTE, "Mute / unmute"),
    "volume_set": (Feature.VOLUME_SET, "Set volume"),
}


def controls(state):
    if state is None:
        return []
    supported = int(state.attributes.get("supported_features", 0))
    return [
        {"action": action, "label": label}
        for action, (feature, label) in FEATURES.items()
        if supported & feature
    ]


def action_data(state, action, form):
    if state is None or state.state in {"unknown", "unavailable"}:
        raise ValueError("Media player is unavailable")
    if action not in {item["action"] for item in controls(state)}:
        raise ValueError("Media action is not permitted or supported")
    data = {"entity_id": state.entity_id}
    if action == "volume_mute":
        data["is_volume_muted"] = not bool(state.attributes.get("is_volume_muted", False))
    elif action == "volume_set":
        volume = float(form.get("volume", ""))
        if not math.isfinite(volume) or not 0 <= volume <= 100:
            raise ValueError("Volume must be between 0 and 100")
        data["volume_level"] = volume / 100
    return data
