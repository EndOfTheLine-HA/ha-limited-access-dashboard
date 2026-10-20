# Advanced setup and development

This page covers the standalone Docker application and contributor workflow.
Most Home Assistant users should follow the HACS instructions in the main
[README](../README.md).

## Moving from the original YAML preview

Version 0.3.0 can import the original single-dashboard YAML setup. Restart Home
Assistant once with the old `limited_access_dashboard:` configuration still in
place. After the dashboard appears under **Settings → Devices & services**,
remove that YAML include and restart again. Future changes can be made with the
integration's **Configure** button.

## Standalone Docker application

The standalone application sends fixed payloads to allowlisted, local-only Home
Assistant webhooks. It supports multiple accounts and reusable access profiles.

1. Copy `config.example.yml` to `config.yml`.
2. Copy the relevant package under `examples/home-assistant/` into Home
   Assistant and replace the example entities.
3. Generate each password hash:

   ```shell
   pip install -e .
   ha-limited-access-dashboard hash-password
   ```

4. Start from `.env.example`. Put password hashes and webhook IDs in `.env`,
   and never commit that file.
5. Validate the configuration:

   ```shell
   ha-limited-access-dashboard check-config config.yml
   ```

6. Start the application:

   ```shell
   docker compose up -d --build
   ```

The Compose file binds to `127.0.0.1:8787` by default. Place the application
behind a maintained HTTPS reverse proxy before making it remotely accessible.

## Standalone configuration model

- `actions` contain fixed names, webhook destinations, and payloads.
- `profiles` select which actions are available and when they may be used.
- `accounts` select a profile and may include activation and expiration dates.
- `allowed_webhook_hosts` prevents requests to unexpected hosts.

Absolute timestamps must include a UTC offset. Weekly schedules use the IANA
timezone set under `app.timezone`. Overnight windows such as `22:00-02:00` are
supported.

The browser cannot submit an entity ID, service name, webhook URL, or custom
payload. Home Assistant independently receives the fixed request and should
enforce its own allowlist, interlock, automatic shutdown, and notifications.

## Development

```shell
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"  # Windows
.venv/Scripts/ruff check .
.venv/Scripts/ruff format --check .
.venv/Scripts/pytest -q
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) and
[SECURITY.md](../SECURITY.md) before submitting changes.
