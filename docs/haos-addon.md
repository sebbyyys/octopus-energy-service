# Home Assistant OS app (formerly add-on)

This app wraps the verified Octopus Energy API. It runs on **Home Assistant OS**
with Supervisor on amd64 or aarch64. Home Assistant Container/Core alone cannot
install apps. No elevated Supervisor, Core or Docker API access is requested.
Keep Protection mode enabled. There is no ingress or "Open web UI": this is an API,
not a dashboard.

## Install and configure

1. In Home Assistant, open **Settings → Apps → App store → ⋮ → Repositories**.
   Older releases call these **Add-ons → Add-on store**. Add this repository URL:
   `https://github.com/sebbyyys/octopus-energy-service`.
2. Refresh the store, select **Octopus Energy Service**, and choose **Install**.
   Installation builds locally and fetches an integrity-checked pinned public
   backend source archive plus locked Python dependencies; internet access is needed.
3. In the app's **Configuration** tab, set `service_token` to a unique randomly
   generated private token of **at least 32 characters**. The blank default is
   intentionally unusable: the app refuses to start without a valid token.
   The password UI masks the value; the entrypoint enforces the minimum length.
4. Enter `api_key` and `account_number` **together in the Home Assistant UI** to
   enable Octopus account synchronization, or leave both blank for offline/cache-only
   mode. Never send credentials or service tokens in chat, screenshots or issue reports.
   The Octopus API key and the service token are different secrets.
5. Save, **Start**, and inspect the **Log** tab for startup success. Enable
   **Start on boot** if desired (the default is auto). Configuration changes require
   a restart. Errors deliberately do not echo submitted options or secret values.

## Connect Home Assistant

Copy the app's exact **Hostname** from its **Info** tab. Supervisor generates a
repository-specific identifier; do not guess it from this repository's name or slug.

For the Octopus Energy Service integration, use:

- **API URL**: `http://<hostname-from-Info>:8080` — the root URL, without `/v1` or
  any endpoint suffix. Replace the placeholder with the actual Info hostname.
- **Service token**: the same `service_token`, **without a `Bearer ` prefix**.
  The integration adds the authentication header itself.

The API listens on the internal Supervisor network. **Network → 8080/tcp is blank
by default**, so no LAN port is published. Home Assistant Core can use the internal
hostname without enabling this mapping. A desktop browser normally cannot resolve
that internal hostname. Do not use `localhost`: inside Core it refers to Core, not
this app. No API permissions are needed for Core to make ordinary outbound HTTP
requests to the app.

If deliberately using an external client, explicitly enable a host port in the
app's Network settings and use `http://<HA-host>:<published-port>`. Restrict access
with your network firewall; HTTP is not encrypted. Do not publish this API to the
internet or forward it through your router. Prefer the internal network.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `service_token` | blank; required before start | Private token, at least 32 characters |
| `api_key`, `account_number` | both blank | Both present for sync, or both blank for offline mode |
| `timezone` | `Europe/London` | Valid IANA timezone; local calendar periods respect DST |
| `sync_interval_seconds` | `1800` | Integer, 300–86400 seconds |
| `initial_days` | `90` | Initial history range, 1–730 days |
| `lookback_days` | `7` | Re-fetch corrections over 1–730 days |
| `payment_method` | `DIRECT_DEBIT` | `DIRECT_DEBIT` or `NON_DIRECT_DEBIT` |
| `gas_units` | `[]` | List of unique `meter_id` / `unit` entries, units `kwh` or `m3` |
| `calorific_value` | `39.2` | Estimated m3 conversion factor, greater than zero, at most 100 |
| `correction_factor` | `1.02264` | Estimated gas correction, greater than zero, at most 2 |

Get the exact gas meter ID from the authenticated `/v1/meters` API. Configure, for
example (placeholder IDs, not real account data):

```yaml
gas_units:
  - meter_id: "gas:YOUR_MPRN:YOUR_SERIAL"
    unit: m3
```

The entrypoint converts this list into the backend's `OCTOPUS_GAS_UNITS_JSON`
dictionary. Confirm units against your meter/supplier readings; never guess SMETS1
kWh versus SMETS2 m3. Unknown gas units leave affected energy/cost values unavailable.

## Persistent history, manual backup and restore

SQLite is stored at **`/data/history/octopus.sqlite3`**, including SQLite sidecar
files while running. `/data` is Supervisor-managed persistent app storage; do not
store history in the image or delete the app as an update procedure. Options are
read from `/data/options.json`; they are not command-line arguments or logged.
Startup briefly runs as root to make options private, preserve the `/data` mount
owner's UID while assigning its group to **10001** with mode **0710** (service
traversal only), and set history ownership to UID/GID **10001**, then drops
supplementary groups, GID and UID before replacing
itself with a single uvicorn worker. History directories are mode 0700, files 0600,
and the process umask is 0077. Docker init is enabled and SIGTERM reaches uvicorn
for graceful SQLite/scheduler shutdown.

Before upgrades, create a **manual Home Assistant backup that includes this app**
under **Settings → System → Backups**. Download/store the backup somewhere separate
and protect its credentials/history. `backup: cold` asks Supervisor to stop the app
for a consistent backup, then restart it. This app does not create or schedule backups.
Restore the app and its data from that backup using Home Assistant's backup UI; then
check configuration, start it and verify status/history before replacing your good copy.
For a manual filesystem copy, stop the app first and preserve the entire history
subdirectory (including any SQLite sidecars), not just a live `.sqlite3` file.

Service history is **not automated Home Assistant historical statistics backfill**.
The integration polls delayed/corrected readings; old API history does not recreate
past recorder samples. Costs and gas conversion are estimates, not invoices. No live
power, account balance/payments, intelligent dispatch billing adjustments or Economy 7
pricing is promised.

## Build and version updates

The app directory is a self-contained Docker build context. Intentionally there is
**no duplicated backend source or vendored wheel**. Docker fetches:

- Source: `https://codeload.github.com/sebbyyys/octopus-energy-service/tar.gz/c32e8fcd83e90b17a8bb140a23162ab896b57f6b`
- Backend commit: `c32e8fcd83e90b17a8bb140a23162ab896b57f6b` (backend 0.1.0)
- SHA256: `7ae95a19f9e0593a1332f9992a467525beda50e73d2706aeadaf63e60af68bc3`
- App version: **0.2.0**; Python **3.13-slim**, uv **0.11.6**

The archive checksum is verified **before extraction**. Dependencies are installed
with `uv sync --locked --no-dev --no-editable`; the non-editable backend installation
is copied into the runtime image. This source pin intentionally remains independent
of future repository HEAD changes. An app backend upgrade must update the source
commit **and freshly verified checksum**, revision label, tests and documentation,
then bump the app version after verification. Do not substitute a moving branch URL.
The Python minor-version image tag and OS package repositories are not digest pinned;
this is a locked backend/dependency build, not a claim of byte-identical image layers.

From the repository root, with a working Docker daemon:

```sh
docker build --build-arg BUILD_ARCH=amd64 \
  --build-arg BUILD_VERSION=0.2.0 \
  -t octopus-energy-service-app:0.2.0 addons/octopus_energy_service
uv run --locked pytest tests/test_addon.py
uv run --locked ruff check addons/octopus_energy_service tests/test_addon.py
```

HA app format references: [configuration](https://developers.home-assistant.io/docs/apps/configuration/)
and [repositories](https://developers.home-assistant.io/docs/apps/repository/).
