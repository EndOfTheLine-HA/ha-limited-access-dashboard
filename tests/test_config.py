from datetime import UTC, datetime
from pathlib import Path

import pytest

from limited_access_dashboard.config import ConfigError, WeeklySchedule, load_settings
from limited_access_dashboard.security import hash_password


def test_overnight_schedule_uses_previous_day_window() -> None:
    schedule = WeeklySchedule.from_value({"monday": ["22:00-02:00"]})
    assert schedule is not None
    assert schedule.allows(datetime(2026, 10, 20, 1, 0, tzinfo=UTC))
    assert not schedule.allows(datetime(2026, 10, 20, 3, 0, tzinfo=UTC))


def test_webhook_host_must_be_explicitly_allowlisted(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text(
        f"""
app:
  allowed_webhook_hosts: [homeassistant.local]
actions:
  unsafe:
    webhook_url: https://example.com/api/webhook/not-allowed
profiles:
  guest:
    actions: [unsafe]
accounts:
  alice:
    profile: guest
    password_hash: "{hash_password("correct horse battery staple")}"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="outside allowed_webhook_hosts"):
        load_settings(config)
