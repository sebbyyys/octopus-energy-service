# Security and privacy

[Back to README](../README.md)

This service handles private meter/account identifiers and household usage patterns.
Treat SQLite data, CSV exports, backups, configuration and HA attributes as sensitive.
The upstream Octopus key uses HTTP Basic authentication to private Octopus endpoints;
the independent service token protects `/v1/*`. Never substitute one for the other.
Public supplier endpoints do not require your account key. Upstream pagination is
restricted to approved Octopus HTTPS origin/paths to avoid forwarding credentials
elsewhere; errors are sanitized rather than exposing upstream response bodies.

## Trust boundary

- Default host binding is loopback; authentication is mandatory even locally.
- HTTP Bearer authentication is **not encryption**. For network clients use TLS via a
  trusted reverse proxy or a protected encrypted tunnel/VPN. Plain HTTP over an
  untrusted LAN can disclose the token and usage data.
- Do not publish port 8080 to the public internet, enable permissive port forwarding
  or disable certificate verification. Restrict access to specific HA/client addresses.
- `/health/live` is unauthenticated process liveness only. Interactive API docs and
  unprotected `/openapi.json` are disabled; `/v1/openapi.json` requires the Bearer token.
  No balance/invoice/payment endpoints exist.
- Token authorization is all-or-nothing, not per-user scopes. Anyone with it can read
  usage and request sync. No built-in rate limiting, TLS termination, SSO, CSRF browser
  UI, per-user audit system or at-rest encryption is claimed. Use proxy/network controls.

## Secrets and rotation

Use a fresh random token of at least 32 characters (README's generation command writes
it without printing). Keep `.env` and HA `secrets.yaml` owner-readable only where
possible. YAML `!secret` separates secrets from the package but does not encrypt them.
Docker environment values are visible to authorized Docker administrators; host/Docker
access is trusted. Backups must be protected separately.

To rotate: generate a new local service token, update `.env`, recreate/restart the
service (Compose: `docker compose up -d --force-recreate`), and update/reload every
HA/client secret. The old token stops working after restart. Rotate a disclosed
Octopus API key through your supplier account as well; it is a separate credential.
Do not paste tokens/API keys in chats, issues, command arguments, URLs or screenshots.

The Docker image excludes `.env`, actual HA secrets, databases and backups and runs
as UID/GID 10001 with a read-only root filesystem, no Linux capabilities and
`no-new-privileges`. These controls reduce risk, not eliminate host access or guarantee
SQLite encryption. See [deployment](deployment.md) for persistent-volume ownership.

## Publication approval

The GitHub Actions workflow is a local repo-ready file, not an active integration.
No repository creation, GitHub OAuth/app setup, remote configuration, commit/push,
artifact upload or image publication is authorized by local setup. Obtain explicit
approval before each external publication/integration step. Never publish a database,
real `.env`, real HA secrets or private exports. Review ignore rules before any push.
