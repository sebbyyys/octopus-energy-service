# Octopus Energy Service app

## Install and start

Use Home Assistant OS (Supervisor), amd64 or aarch64. Open **Settings → Apps →
App store → ⋮ → Repositories**, add `https://github.com/sebbyyys/octopus-energy-service`,
refresh, and install **Octopus Energy Service**. Older releases label these
**Add-ons → Add-on store**. Installation downloads a pinned public backend archive
and locked dependencies and builds locally, so it needs internet access.

In **Configuration**, enter a unique random `service_token` of **at least 32
characters**. The default is deliberately blank and startup rejects it. The password
field masks it; runtime validates its length. Enter your Octopus `api_key` and
`account_number` **together in this UI**, or leave both blank for offline/cache-only
mode. Never enter secrets in chat or include them in screenshots/issues. The
supplier API key is not the service token. Save, **Start**, and check **Log**. Restart
after configuration changes. Start on boot defaults to enabled.

No ingress or Open web UI is provided: this is an API, not a UI. Leave Protection
mode enabled. The app requests no elevated Supervisor/Core/Docker API permissions
and does not use host networking.

## Pair the integration

Copy the exact **Hostname** from this app's **Info** tab; do not guess a repository
hash or slug prefix. Configure the Octopus Energy Service integration with:

- API URL: `http://<hostname-from-Info>:8080`, the **root URL**, without `/v1`.
- Service token: the same token, **without a `Bearer ` prefix**. The integration
  supplies the authorization header.

Network port **8080/tcp is disabled (blank) by default**. Core can access this
internal Supervisor hostname without publishing a port. `localhost` in Core is
not this app. Your desktop browser normally cannot resolve the internal hostname.
If deliberately publishing a LAN port, use the HA host and published port, apply
firewall restrictions, and remember HTTP is unencrypted. Never expose it publicly.

## Options

- `timezone`: valid IANA name, normally `Europe/London`; local periods respect DST.
- `sync_interval_seconds`: integer 300–86400, default 1800; readings are delayed.
- `initial_days`: integer 1–730, default 90; service history fetched initially.
- `lookback_days`: integer 1–730, default 7; correction re-fetch window.
- `payment_method`: `DIRECT_DEBIT` (default) or `NON_DIRECT_DEBIT`.
- `gas_units`: list of unique `{meter_id, unit}` entries, empty by default. Get
  exact IDs from the authenticated `/v1/meters` API. Example with placeholders:

```yaml
gas_units:
  - meter_id: "gas:YOUR_MPRN:YOUR_SERIAL"
    unit: m3
```

Use `kwh` or `m3` verified against your supplier/meter; do not guess. The list is
mapped into the backend JSON dictionary. Unknown units make affected totals
unavailable. `calorific_value` defaults to 39.2 (greater than zero, max 100), and
`correction_factor` defaults to 1.02264 (greater than zero, max 2).

## Data and manual backups

History persists under **`/data/history/octopus.sqlite3`**. Options are read from
`/data/options.json`; they are not logged or passed in command arguments. Root
startup sets options mode 0600 and `/data` mode 0710 with group 10001 (traversal
only; original owner UID preserved), and history ownership UID/GID **10001**, then drops
supplementary groups/GID/UID and execs one uvicorn worker. History directories are
0700, files 0600; umask is 0077. Docker init forwards SIGTERM for graceful shutdown.

Before updates, manually create a backup under **Settings → System → Backups**
that includes this app. Download/protect a separate copy; it contains sensitive
options and account history. The app uses **cold backups**: Supervisor stops it
for backup and restarts it afterwards. The app does not schedule backups.
Restore the app and its data with the Home Assistant backup UI; check settings,
start and verify history. If manually copying files, stop first and preserve the
entire history directory, including SQLite sidecars, rather than copying a live DB.

The API's stored history **does not automatically backfill Home Assistant historical
statistics**. Polling cannot recreate old recorder samples. Costs/gas conversions
are estimates, not invoices. No live power, account balance/payments, intelligent
dispatch billing adjustments or Economy 7 pricing is promised.

## Build provenance

App version **0.2.0**, backend **0.1.0** at commit
`c32e8fcd83e90b17a8bb140a23162ab896b57f6b`.

Archive: `https://codeload.github.com/sebbyyys/octopus-energy-service/tar.gz/c32e8fcd83e90b17a8bb140a23162ab896b57f6b`

SHA256: `7ae95a19f9e0593a1332f9992a467525beda50e73d2706aeadaf63e60af68bc3`

The Dockerfile uses the app directory as its only context, Python 3.13-slim and uv
0.11.6. It verifies this archive **before extraction** and performs a locked,
no-dev, non-editable install. Fetching the pinned backend intentionally avoids
copying backend code or vendoring wheels. Upgrades must update both commit pin and
freshly verified archive checksum, revision label/tests/docs, and the app version.
Python's minor-version tag and OS packages are not image-digest pinned.

Full documentation and local build commands:
[HAOS app guide](https://github.com/sebbyyys/octopus-energy-service/blob/main/docs/haos-addon.md).
