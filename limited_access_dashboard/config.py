from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml


class ConfigError(ValueError):
    """Raised when dashboard configuration is unsafe or invalid."""


_ENV_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        missing: set[str] = set()

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                missing.add(name)
                return match.group(0)
            return os.environ[name]

        expanded = _ENV_PATTERN.sub(replace, value)
        if missing:
            raise ConfigError(f"Missing environment variables: {', '.join(sorted(missing))}")
        return expanded
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def _aware_datetime(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ConfigError(f"{field_name} must include a UTC offset")
    return parsed


def _time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid time {value!r}; use HH:MM") from exc


@dataclass(frozen=True)
class WeeklySchedule:
    windows: dict[int, tuple[tuple[time, time], ...]]

    @classmethod
    def from_value(cls, value: Any) -> WeeklySchedule | None:
        if value in (None, "always"):
            return None
        if not isinstance(value, dict):
            raise ConfigError("profile schedule must be 'always' or a weekday mapping")
        windows: dict[int, tuple[tuple[time, time], ...]] = {}
        for day_name, ranges in value.items():
            normalized = str(day_name).lower()
            if normalized not in _DAYS:
                raise ConfigError(f"Unknown weekday: {day_name}")
            parsed_ranges: list[tuple[time, time]] = []
            for item in ranges or []:
                try:
                    start_raw, end_raw = str(item).split("-", maxsplit=1)
                except ValueError as exc:
                    raise ConfigError(f"Invalid schedule window: {item!r}") from exc
                parsed_ranges.append((_time(start_raw.strip()), _time(end_raw.strip())))
            windows[_DAYS.index(normalized)] = tuple(parsed_ranges)
        return cls(windows=windows)

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
class DashboardAction:
    action_id: str
    name: str
    description: str
    webhook_url: str
    payload: dict[str, Any]
    confirmation: str | None
    cooldown_seconds: int
    accent: str


@dataclass(frozen=True)
class Profile:
    profile_id: str
    name: str
    description: str
    action_ids: tuple[str, ...]
    schedule: WeeklySchedule | None


@dataclass(frozen=True)
class Account:
    username: str
    display_name: str
    password_hash: str
    profile_id: str
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
    session_hours: int
    cookie_secure: bool
    database_path: Path
    request_timeout_seconds: float
    allowed_webhook_hosts: frozenset[str]
    actions: dict[str, DashboardAction]
    profiles: dict[str, Profile]
    accounts: dict[str, Account]

    def account_access(self, account: Account, now: datetime) -> tuple[bool, str]:
        if not account.is_active(now):
            return False, "This account is not active at this time."
        profile = self.profiles[account.profile_id]
        local_now = now.astimezone(self.timezone)
        if profile.schedule is not None and not profile.schedule.allows(local_now):
            return False, "Controls are outside this profile's permitted hours."
        return True, ""


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Unable to read {config_path}: {exc}") from exc
    raw = _expand_env(raw)
    app = raw.get("app", {})
    try:
        timezone = ZoneInfo(str(app.get("timezone", "UTC")))
    except Exception as exc:
        raise ConfigError(f"Invalid timezone: {app.get('timezone')}") from exc
    allowed_hosts = frozenset(str(host).lower() for host in app.get("allowed_webhook_hosts", []))
    if not allowed_hosts:
        raise ConfigError("app.allowed_webhook_hosts must contain at least one HA host")

    actions: dict[str, DashboardAction] = {}
    for action_id, item in (raw.get("actions") or {}).items():
        action_id = str(action_id)
        url = str(item.get("webhook_url", ""))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError(f"Action {action_id} has an invalid webhook URL")
        if parsed.hostname.lower() not in allowed_hosts:
            raise ConfigError(f"Action {action_id} targets a host outside allowed_webhook_hosts")
        if not parsed.path.startswith("/api/webhook/"):
            raise ConfigError(f"Action {action_id} must target /api/webhook/<id>")
        actions[action_id] = DashboardAction(
            action_id=action_id,
            name=str(item.get("name", action_id.replace("_", " ").title())),
            description=str(item.get("description", "")),
            webhook_url=url,
            payload=dict(item.get("payload") or {}),
            confirmation=str(item["confirmation"]) if item.get("confirmation") else None,
            cooldown_seconds=max(0, int(item.get("cooldown_seconds", 2))),
            accent=str(item.get("accent", "green")),
        )
    if not actions:
        raise ConfigError("At least one action is required")

    profiles: dict[str, Profile] = {}
    for profile_id, item in (raw.get("profiles") or {}).items():
        profile_id = str(profile_id)
        action_ids = tuple(str(action) for action in item.get("actions", []))
        unknown = set(action_ids) - set(actions)
        if unknown:
            raise ConfigError(f"Profile {profile_id} references unknown actions: {sorted(unknown)}")
        profiles[profile_id] = Profile(
            profile_id=profile_id,
            name=str(item.get("name", profile_id.replace("_", " ").title())),
            description=str(item.get("description", "")),
            action_ids=action_ids,
            schedule=WeeklySchedule.from_value(item.get("schedule", "always")),
        )
    if not profiles:
        raise ConfigError("At least one profile is required")

    accounts: dict[str, Account] = {}
    for username, item in (raw.get("accounts") or {}).items():
        normalized = str(username).strip().lower()
        if not normalized or normalized in accounts:
            raise ConfigError(f"Invalid or duplicate account name: {username!r}")
        profile_id = str(item.get("profile", ""))
        if profile_id not in profiles:
            raise ConfigError(f"Account {normalized} references unknown profile {profile_id}")
        password_hash = str(item.get("password_hash", ""))
        if not password_hash.startswith("$argon2"):
            raise ConfigError(f"Account {normalized} must use an Argon2 password hash")
        accounts[normalized] = Account(
            username=normalized,
            display_name=str(item.get("display_name", normalized)),
            password_hash=password_hash,
            profile_id=profile_id,
            active_from=_aware_datetime(item.get("active_from"), f"{normalized}.active_from"),
            expires_at=_aware_datetime(item.get("expires_at"), f"{normalized}.expires_at"),
        )
    if not accounts:
        raise ConfigError("At least one account is required")

    session_hours = int(app.get("session_hours", 12))
    if session_hours < 1 or session_hours > 168:
        raise ConfigError("app.session_hours must be between 1 and 168")
    return Settings(
        title=str(app.get("title", "Limited Access Dashboard")),
        timezone=timezone,
        session_hours=session_hours,
        cookie_secure=bool(app.get("cookie_secure", True)),
        database_path=Path(str(app.get("database_path", "data/dashboard.db"))),
        request_timeout_seconds=float(app.get("request_timeout_seconds", 5)),
        allowed_webhook_hosts=allowed_hosts,
        actions=actions,
        profiles=profiles,
        accounts=accounts,
    )


def session_expiry(settings: Settings, now: datetime) -> datetime:
    return now + timedelta(hours=settings.session_hours)
