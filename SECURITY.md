# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's
private vulnerability reporting feature for this repository.

## Security boundary

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

Until the first stable release, only the latest tagged version is supported.

