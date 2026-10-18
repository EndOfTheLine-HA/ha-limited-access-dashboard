# HA Guest Portal

Least-privilege, scheduled guest controls for Home Assistant.

HA Guest Portal gives a child, contractor, cleaner, sitter, Airbnb/Vrbo guest,
or visitor a small mobile interface containing only the controls you explicitly
delegate. It does not expose a Home Assistant login, dashboard, token, entity
registry, search, history, or settings.

> **Project status:** early MVP. Test on non-critical devices before production.
> Do not use it for locks, gates, garage doors, alarms, heaters, or other safety-
> critical controls without an independent security review and physical failsafes.

## Why this exists

A dedicated HA dashboard and Kiosk Mode can make a guest interface look limited,
but visual hiding is not authorization. This portal uses a smaller boundary:

1. The owner declares fixed actions on the server.
2. An account receives a profile containing specific action IDs.
3. Date ranges and weekly schedules are enforced on every request.
4. The server calls a local-only HA webhook with a fixed payload.
5. HA independently validates the request and applies physical safety rules.

The browser can never submit an entity ID, service name, webhook URL, or custom
payload.

## Included access patterns

- **Child:** persistent login, selected household controls, optional daily hours.
- **Contractor/cleaner:** selected controls during recurring work windows.
- **Landscaper:** bounded sprinkler tests with one zone at a time and auto-stop.
- **Airbnb/Vrbo guest:** reservation-specific credentials with check-in/check-out
  activation timestamps.
- **Visitor:** a narrow account that can be deleted immediately after use.

The MVP uses manually configured reservation dates. Read-only iCalendar import
for Airbnb/Vrbo is planned after the authentication and revocation model matures.

## Architecture

```text
Guest browser
    │ HTTPS, username/password, CSRF-protected session
    ▼
HA Guest Portal
    │ fixed server-side payload to an allowlisted HA host
    ▼
Local-only Home Assistant webhook
    │ HA-side allowlist, timer, interlock, audit/notification
    ▼
Selected device or helper
```

No Home Assistant long-lived access token is required.

## Quick start

1. Copy `config.example.yml` to `config.yml`.
2. Copy the relevant package under `examples/home-assistant/` into HA, replace
   the example entities, and generate a unique webhook ID in `secrets.yaml`.
3. Generate each password hash:

   ```shell
   pip install -e .
   ha-guest-portal hash-password
   ```

4. Put password hashes and webhook IDs in `.env`. Do not put them in `config.yml`.
   Start from `.env.example`; do not commit the resulting `.env` file.
5. Validate:

   ```shell
   ha-guest-portal check-config config.yml
   ```

6. Start the container and proxy it through HTTPS:

   ```shell
   docker compose up -d --build
   ```

   A minimal Caddy example is in `examples/Caddyfile`.

   The Compose defaults bind to `127.0.0.1:8787`. Set `PORTAL_BIND_IP` and
   `PORTAL_PORT` in `.env` only when you intentionally need a different bind.

## Home Assistant-native installation

For Home Assistant OS or Supervised installations using Home Assistant Cloud,
the optional custom integration under `custom_components/ha_guest_portal/`
serves the portal directly from Home Assistant at `/guest-portal`. This allows
the same path to travel through the owner's Nabu Casa remote URL without giving
the guest a Home Assistant user account.

1. Copy `custom_components/ha_guest_portal` into `/config/custom_components/`.
2. Copy `examples/home-assistant/ha_guest_portal.yaml` to `/config/`, replace
   the example switch entities, and add the password hash to `secrets.yaml`.
3. Add `ha_guest_portal: !include ha_guest_portal.yaml` to
   `configuration.yaml`.
4. Check the Home Assistant configuration and restart Core.
5. Open `https://<your-nabu-domain>.ui.nabu.casa/guest-portal`.

The HA-native version calls Home Assistant services internally, retains its own
session, CSRF, schedule, rate-limit, and audit boundaries, and never gives the
guest general Home Assistant API access. The initial native action adapter is
for Rachio zone switches and `rachio.start_multiple_zone_schedule`.

## Configuration model

- `actions` define immutable names, webhook destinations, and JSON payloads.
- `profiles` select action IDs and optional weekly time windows.
- `accounts` select one profile and may add absolute activation/expiry timestamps.
- `allowed_webhook_hosts` prevents a configuration mistake from targeting an
  unexpected host.

All absolute timestamps must include a UTC offset. Weekly schedules use the
IANA timezone declared under `app.timezone`. Overnight windows such as
`22:00-02:00` are supported.

## Security checklist

- Keep every HA webhook `local_only: true`.
- Use random webhook IDs and treat them like passwords.
- Put secrets only in environment variables.
- Terminate HTTPS at Caddy, NGINX, or another maintained reverse proxy.
- Bind the portal port to loopback or a private container network.
- Keep destructive and access-control devices out of guest profiles.
- Implement auto-off/interlock rules inside HA, not only in the portal.
- Revoke or remove rental and contractor accounts promptly.
- Review `data/portal.db` audit entries after guest access.

See [SECURITY.md](SECURITY.md) before exposing the portal outside the LAN.

## Development

```shell
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"  # Windows
.venv/Scripts/ruff check .
.venv/Scripts/pytest -q
```

## Roadmap

- Admin UI for creating and revoking accounts without editing YAML.
- Hashed, single-use invitation links as an alternative to passwords.
- Read-only Airbnb/Vrbo iCalendar import with explicit approval before account
  creation.
- Home Assistant notifications for successful and denied commands.
- Docker image publishing, signed releases, SBOMs, and dependency scanning.
- Optional live state through a separately scoped transport; no broad HA token.

## License

MIT

This community project is not affiliated with or endorsed by the Home Assistant
project, Nabu Casa, Airbnb, or Vrbo.
