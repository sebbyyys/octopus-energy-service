# Configuration

[Back to README](../README.md)

Settings read environment variables and a UTF-8 `.env` in the process working
directory. Environment values override `.env`. Never commit real `.env` files.
Use [`.env.example`](../.env.example); all names below are exact, case-sensitive
examples using the `OCTOPUS_` prefix. Restart the service after changing settings.

| Variable | Default | Meaning / validation |
| --- | --- | --- |
| `OCTOPUS_SERVICE_TOKEN` | Required | At least 32 characters; generate cryptographically at random. Bearer authentication for service clients. |
| `OCTOPUS_API_KEY` | Empty | Octopus account API key; must be supplied together with account number. |
| `OCTOPUS_ACCOUNT_NUMBER` | Empty | Exact account identifier from Octopus, not a meter point. Both credentials empty enables offline/cache-only mode. |
| `OCTOPUS_DB_PATH` | `./data/octopus.sqlite3` | SQLite path relative to working directory; Compose overrides to `/app/data/octopus.sqlite3`. |
| `OCTOPUS_TIMEZONE` | `Europe/London` | Valid IANA timezone; used for local period boundaries and habit buckets. |
| `OCTOPUS_SYNC_INTERVAL_SECONDS` | `1800` | Polling interval, 300–86400 seconds. One application worker only. |
| `OCTOPUS_INITIAL_DAYS` | `90` | Initial history window, 1–730 days; availability depends on supplier history. |
| `OCTOPUS_LOOKBACK_DAYS` | `7` | Refresh overlap for delayed/corrected readings, 1–730 days. Corrections older than this may require a wider resync. |
| `OCTOPUS_PAYMENT_METHOD` | `DIRECT_DEBIT` | `DIRECT_DEBIT` or `NON_DIRECT_DEBIT`; selects the applicable published price. |
| `OCTOPUS_GAS_UNITS_JSON` | `{}` | JSON object mapping exact gas meter IDs to `kwh` or `m3`. Unknown by default. |
| `OCTOPUS_CALORIFIC_VALUE` | `39.2` | Positive finite estimate, at most 100; affects m³ conversion, not already-kWh readings. |
| `OCTOPUS_CORRECTION_FACTOR` | `1.02264` | Positive finite value, at most 2; affects m³ conversion. |
| `OCTOPUS_BIND_ADDRESS` | `127.0.0.1` | Compose interpolation only, not an application setting or uvicorn host. See deployment before LAN access. |

## Gas units

Read `/v1/meters` after discovery. Copy its **exact** meter ID into the JSON,
using the consumption API's unit confirmed for that meter, not guessed from serial,
account tariff or the meter's physical display:

```dotenv
# Illustrative IDs only; replace with the exact IDs returned for your meters.
OCTOPUS_GAS_UNITS_JSON='{"gas:YOUR_MPRN:YOUR_SERIAL":"m3"}'
```

For data already reported in kWh use `kwh`; applying m³ conversion to kWh is wrong.
Changing units changes analytics of stored raw readings. Unknown units retain raw
history but gas kWh and affected costs remain null with quality warnings. Imperial
volume (`ft3`), automatic calorific-value lookup and meter-unit autodetection are
not supported. Do not relabel imperial readings as m³.

## History and cache

The database stores raw supplier consumption and rates, not Home Assistant statistics.
Refreshes update corrections by interval identity. Keep the same database/volume
if you want cumulative totals to keep the same history baseline. A new/shorter
history, revised meter discovery or supplier corrections can change totals downward.
Offline mode serves cached data but cannot synchronize or fetch current prices;
empty cache has null sensors, and stale cache is marked stale.
