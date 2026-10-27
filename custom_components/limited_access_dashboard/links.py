"""Use HA's configured remote URL, including Home Assistant Cloud."""

from homeassistant.helpers.network import NoURLAvailableError, get_url

from .config import URL_ROOT


def editor_url(slug: str) -> str:
    from urllib.parse import urlencode

    return "/guest-dashboard-editor?" + urlencode({"dashboard": slug})


def dashboard_url(hass, slug: str) -> str:
    path = f"{URL_ROOT}/{slug}"
    try:
        return get_url(hass, prefer_external=True, allow_cloud=True).rstrip("/") + path
    except NoURLAvailableError:
        return path
