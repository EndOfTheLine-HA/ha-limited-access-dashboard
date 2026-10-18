from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DOMAIN = "ha_guest_portal"
DEFAULT_BASE_PATH = "/guest-portal"
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PortalConfigError(ValueError):
    pass


def _aware_datetime(value: Any, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise PortalConfigError(f"{field_name} must include a UTC offset")
    return parsed


def _parse_time(value: Any) -> time:
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise PortalConfigError(f"Invalid time {value!r}; use HH:MM") from exc


@dataclass(frozen=True)
class WeeklySchedule:
    windows: dict[int, tuple[tuple[time, time], ...]]

    @classmethod
    def from_value(cls, value: Any) -> WeeklySchedule | None:
        if value in (None, "always"):
            return None
        if not isinstance(value, dict):
            raise PortalConfigError("schedule must be 'always' or a weekday mapping")
        windows: dict[int, tuple[tuple[time, time], ...]] = {}
        for day_name, ranges in value.items():
            normalized = str(day_name).lower()
            if normalized not in _DAYS:
                raise PortalConfigError(f"Unknown weekday: {day_name}")
            parsed_ranges: list[tuple[time, time]] = []
            for item in ranges or []:
                try:
                    start_raw, end_raw = str(item).split("-", maxsplit=1)
                except ValueError as exc:
                    raise PortalConfigError(f"Invalid schedule window: {item!r}") from exc
                parsed_ranges.append((_parse_time(start_raw.strip()), _parse_time(end_raw.strip())))
            windows[_DAYS.index(normalized)] = tuple(parsed_ranges)
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
class Zone:
    zone_id: str
    name: str
    entity_id: str


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
    timezone: ZoneInfo
    base_path: str
    session_hours: int
    cookie_secure: bool
    database_path: Path
    duration_minutes: int
    schedule: WeeklySchedule | None
    account: Account
    zones: dict[str, Zone]

    def access(self, now: datetime) -> tuple[bool, str]:
        if not self.account.is_active(now):
            return False, "This account is not active at this time."
        if self.schedule is not None and not self.schedule.allows(now.astimezone(self.timezone)):
            return False, "Controls are outside the permitted hours."
        return True, ""


def load_settings(raw: dict[str, Any]) -> Settings:
    try:
        timezone = ZoneInfo(str(raw.get("timezone", "UTC")))
    except Exception as exc:
        raise PortalConfigError(f"Invalid timezone: {raw.get('timezone')}") from exc

    base_path = "/" + str(raw.get("base_path", DEFAULT_BASE_PATH)).strip("/")
    if base_path == "/":
        raise PortalConfigError("base_path cannot be the site root")

    account_raw = raw.get("account") or {}
    username = str(account_raw.get("username", "")).strip().lower()
    password_hash = str(account_raw.get("password_hash", ""))
    if not username:
        raise PortalConfigError("account.username is required")
    if not password_hash.startswith("$argon2"):
        raise PortalConfigError("account.password_hash must be an Argon2 hash")
    account = Account(
        username=username,
        display_name=str(account_raw.get("display_name", username)),
        password_hash=password_hash,
        active_from=_aware_datetime(account_raw.get("active_from"), "account.active_from"),
        expires_at=_aware_datetime(account_raw.get("expires_at"), "account.expires_at"),
    )

    zones: dict[str, Zone] = {}
    for zone_id, item in (raw.get("zones") or {}).items():
        normalized = str(zone_id).strip()
        entity_id = str((item or {}).get("entity_id", "")).strip()
        if not normalized or not entity_id.startswith("switch."):
            raise PortalConfigError(f"Zone {zone_id!r} must reference a switch entity")
        zones[normalized] = Zone(
            zone_id=normalized,
            name=str((item or {}).get("name", normalized.replace("_", " ").title())),
            entity_id=entity_id,
        )
    if not zones:
        raise PortalConfigError("At least one zone is required")

    session_hours = int(raw.get("session_hours", 8))
    duration_minutes = int(raw.get("duration_minutes", 5))
    if not 1 <= session_hours <= 168:
        raise PortalConfigError("session_hours must be between 1 and 168")
    if not 1 <= duration_minutes <= 60:
        raise PortalConfigError("duration_minutes must be between 1 and 60")

    return Settings(
        title=str(raw.get("title", "Guest Home Controls")),
        timezone=timezone,
        base_path=base_path,
        session_hours=session_hours,
        cookie_secure=bool(raw.get("cookie_secure", True)),
        database_path=Path(str(raw.get("database_path", "/config/guest-portal-data/portal.db"))),
        duration_minutes=duration_minutes,
        schedule=WeeklySchedule.from_value(raw.get("schedule", "always")),
        account=account,
        zones=zones,
    )
