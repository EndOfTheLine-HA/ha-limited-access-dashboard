from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from limited_access_dashboard.app import create_app
from limited_access_dashboard.security import hash_password


def _write_config(
    path: Path,
    *,
    expires_at: datetime | None = None,
    schedule: str = "always",
) -> None:
    expiry = f'    expires_at: "{expires_at.isoformat()}"\n' if expires_at else ""
    path.write_text(
        f"""
app:
  title: Test Dashboard
  timezone: UTC
  cookie_secure: false
  database_path: "{(path.parent / "dashboard.db").as_posix()}"
  allowed_webhook_hosts: [homeassistant.local]
actions:
  allowed_action:
    name: Allowed
    webhook_url: http://homeassistant.local:8123/api/webhook/secret
    payload: {{action: start, zone: test}}
profiles:
  guest:
    name: Guest
    actions: [allowed_action]
    schedule: {schedule}
accounts:
  alice:
    display_name: Alice
    profile: guest
    password_hash: "{hash_password("correct horse battery staple")}"
{expiry}""",
        encoding="utf-8",
    )


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_login_and_only_assigned_action_is_callable(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    _write_config(config)
    calls: list[dict[str, str]] = []

    async def sender(action, timeout):  # type: ignore[no-untyped-def]
        calls.append(action.payload)
        return 200

    client = TestClient(create_app(config, webhook_sender=sender))
    login_page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "correct horse battery staple",
            "csrf_token": _csrf(login_page.text),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Hello, Alice" in response.text

    bad_csrf = client.post(
        "/actions/allowed_action",
        data={"csrf_token": "not-the-session-token"},
        follow_redirects=False,
    )
    assert bad_csrf.status_code == 303
    assert calls == []

    action_response = client.post(
        "/actions/allowed_action",
        data={"csrf_token": _csrf(response.text)},
        follow_redirects=False,
    )
    assert action_response.status_code == 303
    assert calls == [{"action": "start", "zone": "test"}]

    denied = client.post(
        "/actions/not_assigned",
        data={"csrf_token": _csrf(response.text)},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert calls == [{"action": "start", "zone": "test"}]


def test_expired_account_cannot_login(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    _write_config(config, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    client = TestClient(create_app(config))
    login_page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "correct horse battery staple",
            "csrf_token": _csrf(login_page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "invalid_credentials" in response.headers["location"]


def test_login_requires_csrf(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    _write_config(config)
    client = TestClient(create_app(config))
    response = client.post(
        "/login",
        data={"username": "alice", "password": "correct horse battery staple", "csrf_token": "bad"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "invalid_request" in response.headers["location"]


def test_profile_schedule_is_enforced_on_action(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    _write_config(config, schedule="{}")
    calls: list[dict[str, str]] = []

    async def sender(action, timeout):  # type: ignore[no-untyped-def]
        calls.append(action.payload)
        return 200

    client = TestClient(create_app(config, webhook_sender=sender))
    login_page = client.get("/login")
    dashboard = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "correct horse battery staple",
            "csrf_token": _csrf(login_page.text),
        },
        follow_redirects=True,
    )
    assert "outside this profile" in dashboard.text
    denied = client.post(
        "/actions/allowed_action",
        data={"csrf_token": _csrf(dashboard.text)},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert "result=denied" in denied.headers["location"]
    assert calls == []
