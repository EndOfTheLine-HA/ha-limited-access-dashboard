# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's
private vulnerability reporting feature for this repository.

## Native Home Assistant integration

The HA owner chooses approved entities, credentials and access schedules. Guest
requests are authenticated using separate dashboard sessions; the server checks
entity and action permissions, schedules and CSRF tokens. Guests do not receive
a Home Assistant access token. Use HTTPS for guest sign-in.

Only the HA owner can save the main appearance document, opt into guest editing,
or configure custom-card resources. Guest editing changes presentation only.
Custom Lovelace/HACS modules are trusted JavaScript running on the guest page's
origin, not in a sandbox. They can access browser storage available on that
origin. Install only trusted modules and use a separate browser profile when
testing guest cards. Filtering the card's `hass` adapter does not sandbox code.

Back up the integration configuration and `limited-access-dashboard-data` before
upgrading. Treat filesystem access to those files as administrative access.

## Standalone application security boundary

The legacy standalone application has a separate implementation and version.
The native integration's v0.5+ session revocation and aggregate login limits do
not apply to it. Its shorter minimum password policy and process-local limiter
are legacy behavior; prefer the native integration for new installations.

The dashboard can invoke only actions declared by the server owner in `config.yml`.
The browser cannot supply a Home Assistant entity ID, service name, webhook URL,
or payload. Home Assistant webhooks should remain `local_only: true` and should
enforce their own allowlist, automatic shutdown, and other physical safety rules.

This project does not make Home Assistant webhooks authenticated. Their IDs are
secrets. Store them in environment variables, never in the public configuration,
logs, screenshots, issue reports, or source control.

Run the standalone dashboard behind HTTPS. Bind its application port to loopback or a private
container network so users cannot bypass the TLS reverse proxy. Treat access to
the host, container configuration, database, or environment as administrator
access to the dashboard.

The built-in rate limiter is intentionally small and process-local. Run one app
worker, as the supplied container does. Public deployments should also apply
connection and request limits at their reverse proxy or firewall.

## Supported versions

Only the latest stable tagged release is supported.

