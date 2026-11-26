"""Use HA's configured remote URL, including Home Assistant Cloud."""

from homeassistant.helpers.network import NoURLAvailableError, get_url

from .config import URL_ROOT, normalize_url_prefix


def editor_url(slug: str) -> str:
    from urllib.parse import urlencode

    return "/guest-dashboard-editor?" + urlencode({"dashboard": slug})


def dashboard_url(hass, slug: str, url_prefix: str = URL_ROOT) -> str:
    path = f"/{normalize_url_prefix(url_prefix)}/{slug}"
    try:
        return get_url(hass, prefer_external=True, allow_cloud=True).rstrip("/") + path
    except NoURLAvailableError:
        return path
