# HACS / native Home Assistant integration

[Back to README](../README.md)

The custom integration is **Octopus Energy Service**, domain
`octopus_energy_service`, version **0.2.0**, for Home Assistant **2026.9.4+**.
It reads your self-hosted service, not the Octopus supplier API directly. The
existing `octopus_energy` integration is separate and must not be replaced,
renamed or removed. This is an independent community project, not official
Octopus branding.

## Install

1. Start the backend service or its add-on and make it reachable from Home
   Assistant. Offline/cache-only operation is supported: supplier credentials
   are not required to configure this integration. The **service token** is
   required even in offline mode.
2. In HACS, open its menu → **Custom repositories**, enter
   `https://github.com/sebbyyys/octopus-energy-service`, select **Integration**,
   then download **Octopus Energy Service**. The integration files must first
   be published to the branch/release selected in HACS; local repository files
   are not a deployment.
3. Restart Home Assistant. Open **Settings → Devices & services → Add
   integration → Octopus Energy Service**.
4. Enter the **root API URL**, for example `http://<service-host>:8080`, and the
   locally configured `OCTOPUS_SERVICE_TOKEN`. Do not enter the supplier API key
   or append `/v1/home-assistant`. The token input is masked.

Manual installation: copy only `custom_components/octopus_energy_service/`
from this repository into `/config/custom_components/octopus_energy_service/`
and restart HA. Do not copy the backend Python package, install FastAPI into HA,
or modify `/config/custom_components/octopus_energy/`.

For HA in a container or on another machine, `127.0.0.1` refers to HA itself,
not the backend. Configure a LAN/internal-container service address and restrict
access with your firewall. Use HTTPS with a valid certificate across untrusted
networks: a masked input does not encrypt HTTP transport. HTTPS certificate
verification is enabled; there is no insecure TLS switch.

## What it creates

One service device and **22 native sensors**: the **21 numeric/timestamp payload
fields** plus the existing REST package's status diagnostic. Exact generated
entity IDs depend on the device name and existing HA entity registry; unique IDs
include this integration's domain, config entry ID and field key. They are
separate from the REST YAML package IDs and the `octopus_energy` integration.

| Sensors | Count | Units / semantics |
| --- | --- | --- |
| Electricity total, today, yesterday, month, current rate | 5 | kWh; p/kWh |
| Gas total, today, yesterday, month, current rate | 5 | kWh; p/kWh |
| Export total, today | 2 | kWh |
| Cost today, yesterday, week, month, previous month estimates | 5 | GBP, estimated net spend |
| Peak hour, baseload estimate | 2 | Local hour 0–23; kW estimate, not live power |
| Last sync, latest reading | 2 | Aware timestamps, normalized to UTC |
| Status | 1 | `ready`, `stale` or `no_data` |

All sensors share one authenticated **GET `/v1/home-assistant` every 300
seconds**, with a 20-second timeout. No synchronization POST, discovery scan,
upstream credentials or device-control calls are made. HA stops polling when
there are no listeners or the entry is unloaded. Its shared HTTP session is
not closed by the integration.

Numeric metrics are unavailable when the request fails, backend data is stale
or unavailable, or that field is missing/null/nonfinite/malformed. Valid zero
and negative rates/costs remain numeric, never replaced with zero. Missing or
malformed groups do not crash unrelated sensors. Timestamp diagnostics remain
available on a successfully fetched stale snapshot if their values are valid
aware timestamps. Naive/invalid/null timestamps are unavailable. Status remains
available for stale/no-data snapshots; a transport/auth/payload-envelope failure
makes all entities unavailable.

Status attributes expose only `backend_available`, `backend_stale` and
`quality_complete`. Freshness is not coverage: a recent sync can still contain
incomplete supplier readings. Known current rates can be shown before readings
arrive if the backend marks them available and fresh.

Cumulative electricity/gas/export energy uses **`state_class: total`**, not
`total_increasing`: corrected backend history may reduce totals. Rolling period
energy/cost sensors do not masquerade as cumulative counters. Delayed polling
does **not** backfill precise historical HA Energy statistics. Costs include
observed recorded usage and prorated standing charges, less export credit; they
are not invoices or necessarily complete-period spend. Unknown gas units/rates
remain unavailable. The backend's [documented limitations](analytics.md) apply.

If replacing the REST YAML package, remove/disable that package's duplicate
sensors only after reviewing your existing dashboards and Energy sources.
Existing REST sensor history is not automatically migrated or merged.

## Reconfigure, reauthenticate and remove

Use the entry menu → **Reconfigure** to change the root URL and token. Existing
entity unique IDs are preserved. A canonical URL already used by another entry
is rejected, including concurrent initial setup flows. URLs with userinfo,
queries, fragments or non-root paths are rejected before requesting anything;
redirects are never followed.

If a token is rejected (401/403), HA starts a reauthentication flow and stops
scheduled polling. Enter the replacement token; the entry is updated and
reloaded. Stored tokens are never prefilled into forms. Removing/disabling the
entry safely unloads the platform and cancels coordinator timers.

Diagnostics deliberately redact the entire connection configuration and never
copy options, raw payloads, consumption values, account/meter identifiers or
quality warning strings. Only validated health flags and timestamps are
returned. HA still stores its config entry token locally; protect HA backups.

## Verification / isolated CI environment

Tests use **installed Home Assistant 2026.9.4**, not fake HA import modules.
That release requires Python **>=3.14.2**; verification used Python **3.14.4**.
The versioned installed config-flow/selector APIs accept `voluptuous`; HA also
ships `probatio`. Both are supplied by HA, not integration requirements. The
production manifest has `requirements: []` and the integration imports neither
`octopus_service` nor FastAPI.

From the repository root, with uv installed:

```sh
uv venv --python 3.14 .venv-ha
uv pip sync --python .venv-ha/bin/python tests/ha_integration/requirements.txt
PYTHONPATH=. .venv-ha/bin/python -m pytest tests/ha_integration -q --allow-hosts=127.0.0.1,::1 --cov=custom_components.octopus_energy_service --cov-report=term-missing
uv run --locked ruff check custom_components/octopus_energy_service tests/ha_integration
```

The pinned test lock is separate from production/backend dependencies. Without
Home Assistant installed, functional HA test modules skip cleanly; distribution
metadata tests still run in the backend suite. Tests exercise real loopback
aiohttp servers, the HA config-flow manager, coordinator scheduling,
reauthentication, native platform/entity/device registries, unload, malformed
payloads and diagnostics redaction. These isolated tests do not imply the
integration has been installed in your live Home Assistant.

Regenerate the test lock intentionally with:

```sh
uv pip compile --python .venv-ha/bin/python tests/ha_integration/requirements.in --output-file tests/ha_integration/requirements.txt
```

The included transparent `brand/icon.png` is original geometric energy-octopus
art, reproducible using `tests/ha_integration/generate_brand.py` in the HA test
environment. It is not the supplier's logo.

## Packaging references

- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)
  specify a single integration directory and self-contained runtime files,
  manifest metadata and a `brand/icon.png` asset.
- [HACS general requirements](https://www.hacs.xyz/docs/publish/start/) describe
  public repositories, repository description/topics/README and root `hacs.json`.
- [HA config flows](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)
  and [coordinated polling](https://developers.home-assistant.io/docs/integration_fetching_data/)
  describe the framework used here. A mutable URL is not used as a config-entry
  unique ID; duplicate URLs are checked as connection data instead.

HACS default-store inclusion and GitHub releases are separate publication
steps. Local tests do not claim HACS store acceptance or remote publication.
