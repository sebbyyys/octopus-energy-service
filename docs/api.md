# API

[Back to README](../README.md)

Base URL defaults to `http://127.0.0.1:8080`. Every `/v1/*` route requires
`Authorization: Bearer <OCTOPUS_SERVICE_TOKEN>`. Missing/wrong tokens return 401.
The service token is never the upstream Octopus API key. `/health/live` is public
liveness only, not proof of current data, credentials or supplier availability.
Production interactive Swagger/ReDoc and unprotected `/openapi.json` are disabled.
The schema can be read at authenticated `/v1/openapi.json`.

## Read-only local requests

Use this local Python example to read the token from `.env` without copying it into
shell history, curl arguments or URLs. Run from the repository root:

```sh
uv run --locked python -c 'from dotenv import dotenv_values; import httpx; token=dotenv_values(".env")["OCTOPUS_SERVICE_TOKEN"]; r=httpx.get("http://127.0.0.1:8080/v1/status", headers={"Authorization": "Bearer " + token}); r.raise_for_status(); print(r.json())'
```

Change only the URL for other GET routes. In deployed environments, load the token
from your configured secret source. Environment variables override `.env` for the
server, so if you override the token you must use the same source for the client.
Never place credentials in query parameters, logs or shared screenshots.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/health/live` | Public process liveness. |
| GET | `/v1/status` | Configuration/synchronization status. |
| GET | `/v1/openapi.json` | Protected OpenAPI schema; interactive docs are disabled. |
| GET | `/v1/meters` | Discovered meters, agreements and explicit gas-unit settings. |
| GET | `/v1/consumption` | Raw normalized readings, with `start`, `end`, optional `meter_id`, `limit`, `offset`. |
| GET | `/v1/rates` | Stored unit/standing rates; same range/filter/pagination parameters. |
| GET | `/v1/analytics` | Cost, energy, daily series, import habits and quality; range and optional meter filter. |
| GET | `/v1/home-assistant` | Stable nested sensor payload, current periods, freshness and quality. |
| GET | `/v1/export` | CSV raw consumption rows; range and optional meter filter. |
| POST | `/v1/sync` | Request background synchronization: 202 accepted, 409 already busy, 503 credentials absent. |

Sync is an explicit side effect (upstream reads/local cache writes). Do not repeatedly
POST to it for health checks. It does not upload data to Octopus or control devices.
With no parameters it uses the initial-history setting, or catches up from the stored
data checkpoint and rechecks the correction-lookback window. Optional `start`/`end`
query parameters request a historical backfill, bounded to 730 days; a future end is
rejected. Successful historical backfills do not replace the latest data checkpoint
with the current wall-clock time. Inspect `/v1/status` for completion or a sanitized
`last_error`: HTTP 202 means queued, not that upstream retrieval has succeeded.

## Range queries

`start` and `end` must be aware ISO 8601 datetimes when supplied, `end > start`, maximum
730 days. Omitted `end` defaults to now and omitted `start` to 30 days before `end`.
Supply both explicitly for reproducible historical reports. For example
`start=2026-01-01T00:00:00Z&end=2026-02-01T00:00:00Z`.
If using `+00:00` in a URL, encode `+` as `%2B` (HTTP client's `params` handles this).
`meter_id` must be an exact discovered identifier, not a tariff code or serial alone.
Paginated reading/rate limits are 1–5000; offset is nonnegative. Follow response
pagination rather than assuming a single page contains a whole requested window.
Naive/reversed/overlarge ranges and invalid pagination are validation errors.

## Home Assistant payload contract

`available` and `stale` are separate from per-field nullability and
`quality.complete`. `last_sync` and `latest_reading` are aware ISO 8601 timestamps or
null. A recent sync may still contain delayed/incomplete supplier readings. The snapshot
is stale if latest reading is over 48 hours old, successful sync is absent, or successful
sync is older than three configured sync intervals. Staleness does not measure coverage;
inspect quality (including per-period quality where supplied) independently.

| Group | Keys | Units |
| --- | --- | --- |
| root | `available`, `stale`, `last_sync`, `latest_reading`, `currency`, `timezone`, `quality` | Flags, timestamps, `GBP`, IANA timezone, quality object |
| `electricity` | `total_kwh`, `today_kwh`, `yesterday_kwh`, `month_kwh`, `current_rate_pence` | kWh / pence per kWh |
| `gas` | Same keys as electricity | kWh / pence per kWh; unknown units mean null gas energy |
| `export` | `total_kwh`, `today_kwh` | kWh |
| `cost` | `today_gbp`, `yesterday_gbp`, `week_gbp`, `month_gbp`, `previous_month_gbp` | Estimated net GBP, including standing charges and subtracting export credit |
| `habits` | `peak_hour`, `baseload_kw` | Local hour integer / estimated kW |

Numeric metrics may be **null**; do not replace null with zero. Zero consumption,
negative prices and negative costs remain valid numeric values. Current rates are
selected from stored applicable rates for now, not from the most recent reading.
Known current rates can be available even before any consumption readings arrive;
energy/cost fields stay null. `cost.*_gbp` fields are observed recorded-usage estimates
plus prorated standing charges, not full-period spend when coverage is incomplete.
Analytics distinguishes strict `totals` from supplementary `observed_totals`.
No account balance, invoice or payment fields are supplied by this service.
CSV contains raw meter-unit consumption, not already-converted gas energy or costs;
join it to meter metadata before interpreting units. Treat exports as private data.
