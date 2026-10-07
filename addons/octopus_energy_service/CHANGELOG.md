# Changelog

## 0.2.0

- Home Assistant OS app wrapping verified backend 0.1.0 from a pinned,
  SHA256-checked public GitHub archive and locked production dependencies.
- Password-masked token/credentials, sanitized option validation and explicit gas units.
- Private persistent history; UID/GID 10001 after root-only initialization.
- Internal API access only by default, cold backups and Docker init signal forwarding.
