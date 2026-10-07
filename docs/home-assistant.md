# Home Assistant package and native dashboard

[Back to README](../README.md)

This is a REST polling package, not a custom integration. No HACS, MQTT, frontend
plugin or live Home Assistant access is needed to inspect these examples. Static
YAML/Jinja tests do not replace checking configuration on your installed HA version.

## Installation

1. Make the service reachable from **Home Assistant's network namespace**. HA OS on
   another machine cannot reach the Docker host at `127.0.0.1`; neither can a separate
   container using its own loopback. Follow [deployment networking](deployment.md).
2. Enable packages by merging into your existing `homeassistant:` block (do not add
   a duplicate top-level block):

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. Copy [octopus.yaml](../examples/home-assistant/octopus.yaml) to
   `/config/packages/octopus.yaml`. If you already use packages, retain the existing
   include structure and merge only this package.
4. Merge the two keys from [secrets.example.yaml](../examples/home-assistant/secrets.example.yaml)
   into `/config/secrets.yaml`. Set `octopus_service_url` to the reachable URL ending
   `/v1/home-assistant`, and `octopus_service_authorization` to `Bearer ` followed by
   your **service token**, not the Octopus API key. Never commit the real secrets file.
5. Run Home Assistant's configuration check, then restart/reload as supported by your
   installed version. Inspect Developer Tools → States and the status sensor's quality
   attributes. If configured for HTTPS, use a trusted certificate; keep `verify_ssl: true`.
6. Optionally create a separate manual YAML dashboard and paste
   [dashboard.yaml](../examples/home-assistant/dashboard.yaml). It uses native cards
   only. Verify the entity IDs against your registry: renamed/pre-existing entities
   may receive different IDs. Keep your existing dashboards unchanged.

The package makes one shared GET every 300 seconds with a 20-second timeout for all
sensors. It does not force an upstream sync every time HA polls.

## Availability and interpretation

All numeric values require `available`, not `stale`, and a non-null individual field.
Missing/non-JSON responses are unavailable. No `float(0)` fallback converts absent
data into false zero energy/cost. Negative prices/costs and actual zeros are preserved.
Timestamp diagnostics remain available when their values are present, including for
stale data; inspect latest reading independently of last sync. Status can report
`ready`, `no_data` or `stale`; status readiness does not prove complete coverage.
Inspect `quality` on `sensor.octopus_service_status` before trusting estimates.
Net-cost sensors show recorded-usage estimates plus applicable standing charges;
missing readings are not priced and can make those values understate full-period
spend. Per-period coverage is included in `quality.periods`. They are not bills.

Gas in kWh has `device_class: energy`, not the gas-volume class. Rates use `p/kWh`,
not `device_class: monetary` (which expects a currency unit). Net period costs use
`device_class: monetary`, unit `GBP`, and deliberately no state class. Baseload uses
power units but is a historical estimate, **not live power**.

## Energy dashboard caveats

Only the three stored-history cumulative energy sensors use `state_class: total`:

- `sensor.octopus_electricity_total` (import)
- `sensor.octopus_gas_total` (gas energy)
- `sensor.octopus_export_total` (export)

They deliberately do **not** use `total_increasing`: supplier corrections can lower
them, and treating a lower value as a reset would fabricate extra consumption.
There is no `last_reset`, because history corrections are not meter resets. Period
energy totals (today/yesterday/month) have no state class/reset attribute; never select
these resetting period snapshots as cumulative Energy sources. Cost snapshots are
not cumulative spend sensors. Do not add standing charges again to net cost.

Even eligible total sensors are not a precise historical billing integration. HA
records changes when it receives them, not at the supplier's original reading time.
Delayed batches, changed history and corrections can misattribute hourly/daily usage.
The first large stored-history total establishes a baseline; it does not backfill
previous HA statistics. Restore the service DB rather than recreating history casually,
and check HA statistics after corrections. Prefer service daily analytics for exact
supplier-period bucketing. No automatic HA external-statistics repair/backfill is provided.

## Troubleshooting

- 401: secret must include exactly the `Bearer ` prefix and the current service token.
- Cannot connect: HA's loopback is not the service host; check bind address/firewall/proxy.
- Unavailable gas: configure exact meter ID and `kwh`/`m3`, then synchronize.
- Unknown current price/cost: missing rates, wrong payment method, unsupported tariff
  or old cache; read quality and status rather than inserting a zero fallback.
- Fresh last sync with old reading: supplier reporting delay is not live consumption.

Official references: [REST shared endpoint](https://www.home-assistant.io/integrations/rest/),
[REST sensor availability](https://www.home-assistant.io/integrations/sensor.rest/),
[sensor state classes](https://developers.home-assistant.io/docs/core/entity/sensor/),
[packages](https://www.home-assistant.io/docs/configuration/packages/).
