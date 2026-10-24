from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DOMAIN = "limited_access_dashboard"
URL_ROOT = "/limited-access-dashboard"
DEFAULT_SESSION_HOURS = 8
DEFAULT_DURATION_MINUTES = 5
DEFAULT_DATA_DIR = "/config/limited-access-dashboard-data"
SUPPORTED_CONTROL_DOMAINS = (
    "button",
    "input_boolean",
    "light",
    "media_player",
    "scene",
    "script",
    "switch",
)
SUPPORTED_DISPLAY_DOMAINS = ("binary_sensor", "climate", "sensor")
SUPPORTED_DOMAINS = SUPPORTED_CONTROL_DOMAINS + SUPPORTED_DISPLAY_DOMAINS
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class DashboardConfigError(ValueError):
    pass


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:48]


def _aware_datetime(value: Any, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise DashboardConfigError(f"{field_name} must include a UTC offset")
    return parsed


def _parse_time(value: Any) -> time:
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise DashboardConfigError(f"Invalid time {value!r}; use HH:MM") from exc


@dataclass(frozen=True)
class WeeklySchedule:
    windows: dict[int, tuple[tuple[time, time], ...]]

    @classmethod
    def from_value(cls, value: Any) -> WeeklySchedule | None:
        if value in (None, "always"):
            return None
        if not isinstance(value, dict):
            raise DashboardConfigError("schedule must be 'always' or a weekday mapping")
        windows: dict[int, tuple[tuple[time, time], ...]] = {}
        for day_name, ranges in value.items():
            normalized = str(day_name).lower()
            if normalized not in DAYS:
                raise DashboardConfigError(f"Unknown weekday: {day_name}")
            parsed_ranges: list[tuple[time, time]] = []
            for item in ranges or []:
                try:
                    start_raw, end_raw = str(item).split("-", maxsplit=1)
                except ValueError as exc:
                    raise DashboardConfigError(f"Invalid schedule window: {item!r}") from exc
                parsed_ranges.append((_parse_time(start_raw.strip()), _parse_time(end_raw.strip())))
            windows[DAYS.index(normalized)] = tuple(parsed_ranges)
        return cls(windows)

    def allows(self, local_now: datetime) -> bool:
        weekday = local_now.weekday()
        current = local_now.time().replace(tzinfo=None)
        for start, end in self.windows.get(weekday, ()):
            if start <= end and start <= current < end:
                return True
            if start > end and current >= start:
                return True
        previous = (weekday - 1) % 7
        for start, end in self.windows.get(previous, ()):
            if start > end and current < end:
                return True
        return False


@dataclass(frozen=True)
class DashboardCard:
    entity_id: str
    card_type: str

    @property
    def action(self) -> str | None:
        if self.card_type == "sprinkler":
            return "sprinkler"
        domain = self.entity_id.split(".", maxsplit=1)[0]
        if domain == "media_player":
            return "media"
        if domain in {"light", "switch", "input_boolean"}:
            return "toggle"
        if domain == "button":
            return "press"
        if domain in {"scene", "script"}:
            return "turn_on"
        return None


@dataclass(frozen=True)
class Account:
    username: str
    display_name: str
    password_hash: str
    active_from: datetime | None
    expires_at: datetime | None

    def is_active(self, now: datetime) -> bool:
        return (self.active_from is None or now >= self.active_from) and (
            self.expires_at is None or now < self.expires_at
        )


@dataclass(frozen=True)
class Settings:
    title: str
    slug: str
    timezone: ZoneInfo
    session_hours: int
    cookie_secure: bool
    database_path: Path
    duration_minutes: int
    schedule: WeeklySchedule | None
    account: Account
    cards: tuple[DashboardCard, ...]

    @property
    def base_path(self) -> str:
        return f"{URL_ROOT}/{self.slug}"

    @property
    def sprinklers(self) -> tuple[DashboardCard, ...]:
        return tuple(card for card in self.cards if card.card_type == "sprinkler")

    def access(self, now: datetime) -> tuple[bool, str]:
        if not self.account.is_active(now):
            return False, "This account is not active at this time."
        if self.schedule is not None and not self.schedule.allows(now.astimezone(self.timezone)):
            return False, "Controls are outside the permitted hours."
        return True, ""


def _schedule_from_flat(raw: dict[str, Any]) -> str | dict[str, list[str]]:
    if bool(raw.get("always_access", True)):
        return "always"
    days = [str(day).lower() for day in raw.get("weekdays", []) if str(day).lower() in DAYS]
    start = str(raw.get("start_time", "08:00"))
    end = str(raw.get("end_time", "18:00"))
    return {day: [f"{start}-{end}"] for day in days}


def _validate_entity(entity_id: str) -> str:
    normalized = str(entity_id).strip().lower()
    try:
        domain, object_id = normalized.split(".", maxsplit=1)
    except ValueError as exc:
        raise DashboardConfigError(f"Invalid entity ID: {entity_id}") from exc
    if domain not in SUPPORTED_DOMAINS or not object_id:
        raise DashboardConfigError(f"Unsupported entity: {entity_id}")
    return normalized


def load_settings(raw: dict[str, Any], *, default_timezone: str = "UTC") -> Settings:
    """Load config-entry data or the legacy YAML format."""
    legacy = isinstance(raw.get("account"), dict)
    if legacy:
        account_raw = raw.get("account") or {}
        username = str(account_raw.get("username", "")).strip().lower()
        slug = normalize_slug(username or str(raw.get("title", "dashboard")))
        entities: list[str] = []
        sprinklers: list[str] = []
        for item in (raw.get("zones") or {}).values():
            entity_id = str((item or {}).get("entity_id", "")).strip()
            if entity_id:
                entities.append(entity_id)
                sprinklers.append(entity_id)
        schedule_value = raw.get("schedule", "always")
        active_from = account_raw.get("active_from")
        expires_at = account_raw.get("expires_at")
        password_hash = str(account_raw.get("password_hash", ""))
        display_name = str(account_raw.get("display_name", username))
        timezone_name = str(raw.get("timezone", default_timezone))
    else:
        username = str(raw.get("username", "")).strip().lower()
        slug = normalize_slug(str(raw.get("slug", username or "dashboard")))
        entities = [str(item) for item in raw.get("entities", [])]
        sprinklers = [str(item) for item in raw.get("sprinkler_entities", [])]
        schedule_value = raw.get("schedule", _schedule_from_flat(raw))
        active_from = raw.get("active_from")
        expires_at = raw.get("expires_at")
        password_hash = str(raw.get("password_hash", ""))
        display_name = str(raw.get("display_name", username))
        timezone_name = str(raw.get("timezone", default_timezone))

    if not slug:
        raise DashboardConfigError("dashboard URL name is required")
    if not username:
        raise DashboardConfigError("username is required")
    if not password_hash.startswith("$argon2"):
        raise DashboardConfigError("password must be stored as an Argon2 hash")

    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise DashboardConfigError(f"Invalid timezone: {timezone_name}") from exc

    sprinkler_set = {_validate_entity(entity_id) for entity_id in sprinklers}
    ordered_entities: list[str] = []
    for entity_id in [*entities, *sprinklers]:
        normalized = _validate_entity(entity_id)
        if normalized not in ordered_entities:
            ordered_entities.append(normalized)
    if not ordered_entities:
        raise DashboardConfigError("At least one entity is required")

    cards = tuple(
        DashboardCard(
            entity_id=entity_id,
            card_type="sprinkler"
            if entity_id in sprinkler_set
            else "control"
            if entity_id.split(".", maxsplit=1)[0] in SUPPORTED_CONTROL_DOMAINS
            else "display",
        )
        for entity_id in ordered_entities
    )

    session_hours = int(raw.get("session_hours", DEFAULT_SESSION_HOURS))
    duration_minutes = int(raw.get("duration_minutes", DEFAULT_DURATION_MINUTES))
    if not 1 <= session_hours <= 168:
        raise DashboardConfigError("session_hours must be between 1 and 168")
    if not 1 <= duration_minutes <= 60:
        raise DashboardConfigError("duration_minutes must be between 1 and 60")

    return Settings(
        title=str(raw.get("title", "Limited Access Dashboard")),
        slug=slug,
        timezone=timezone,
        session_hours=session_hours,
        cookie_secure=bool(raw.get("cookie_secure", True)),
        database_path=Path(str(raw.get("database_path", f"{DEFAULT_DATA_DIR}/{slug}.db"))),
        duration_minutes=duration_minutes,
        schedule=WeeklySchedule.from_value(schedule_value),
        account=Account(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            active_from=_aware_datetime(active_from, "active_from"),
            expires_at=_aware_datetime(expires_at, "expires_at"),
        ),
        cards=cards,
    )


def legacy_to_entry(raw: dict[str, Any], *, default_timezone: str) -> dict[str, Any]:
    """Convert the original YAML structure into config-entry data."""
    settings = load_settings(raw, default_timezone=default_timezone)
    result: dict[str, Any] = {
        "title": settings.title,
        "slug": settings.slug,
        "timezone": str(settings.timezone),
        "username": settings.account.username,
        "display_name": settings.account.display_name,
        "password_hash": settings.account.password_hash,
        "session_hours": settings.session_hours,
        "duration_minutes": settings.duration_minutes,
        "entities": [card.entity_id for card in settings.cards],
        "sprinkler_entities": [card.entity_id for card in settings.sprinklers],
        "schedule": raw.get("schedule", "always"),
        "cookie_secure": settings.cookie_secure,
        "database_path": str(settings.database_path),
    }
    if settings.account.active_from is not None:
        result["active_from"] = settings.account.active_from.isoformat()
    if settings.account.expires_at is not None:
        result["expires_at"] = settings.account.expires_at.isoformat()
    return result
