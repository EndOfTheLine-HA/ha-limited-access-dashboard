"""Register concrete, collision-checked dashboard URL roots."""
from .config import DOMAIN, DashboardConfigError, normalize_url_prefix
from .http import ScopedDashboardManager, register_views
from .editor import register_editor_views


def validate_url_root(hass, value: str) -> str:
    prefix = normalize_url_prefix(value)
    root = f"/{prefix}"
    # HA panels can serve paths under the same root as a dashboard.
    if prefix in hass.data.get("frontend_panels", {}):
        raise DashboardConfigError("URL prefix is already used by a Home Assistant panel")
    manager = hass.data.get(DOMAIN)
    if manager and root in manager.registered_roots:
        return prefix
    # Avoid taking over any existing concrete HTTP namespace. The frontend's
    # dynamic index resource has canonical '/' and is deliberately not matched.
    for resource in hass.http.app.router.resources():
        path = resource.canonical
        if path == root or path.startswith(root + "/"):
            raise DashboardConfigError("URL prefix is already used by Home Assistant")
    return prefix


def register_url_root(hass, manager, value: str) -> None:
    prefix = validate_url_root(hass, value)
    root = f"/{prefix}"
    if root in manager.registered_roots:
        return
    scoped = ScopedDashboardManager(manager, root)
    register_views(hass, scoped)
    register_editor_views(hass, scoped)
    manager.registered_roots.add(root)
