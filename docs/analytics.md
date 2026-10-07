# Estimates, data quality and supported scope

[Back to README](../README.md)

## Periods and units

All API query datetimes must carry UTC offsets. Stored datetimes normalize to UTC;
queries and agreements are half-open `[start, end)`. Local periods and habit buckets
use `OCTOPUS_TIMEZONE`, normally `Europe/London`. A UK clock-change day has 46 or
50 expected half-hour intervals rather than 48; do not assume every day is 24 hours.
Today/month/week sensors describe local calendar periods: today to now, yesterday,
Monday-to-now for week, month-to-now, and the whole previous month, not rolling 24-hour
or 30-day windows. Check the API's period bounds when comparing reports.

Electricity import/export energy is kWh. Gas needs explicit units. For m³:

```text
estimated kWh = m³ × correction_factor × calorific_value ÷ 3.6
```

The defaults are estimates, not a promise of your invoice's daily calorific value.
No conversion is applied to readings explicitly configured as `kwh`.

## Costs

Prices are published **VAT-inclusive pence** per kWh or per day. Costs are GBP.
The applicable rate must match the meter's agreement and selected payment method;
a rate with no payment-method restriction applies to either method. Negative unit
prices and negative net costs are valid, not missing data.

Each original electricity interval is rounded to 0.01 kWh using decimal
`ROUND_HALF_EVEN` before cost splitting. Intervals crossing query/rate/agreement
boundaries are split proportionally, **assuming uniform energy within that original
interval**. The assumption also applies when assigning energy to local habit buckets;
it cannot reconstruct sub-interval behavior.

Standing charges are counted once per meter point per local day, not once per serial
number; meter replacements do not multiply the daily charge. Export meters do not
add standing charges. Partial-day requests prorate standing charges by the elapsed
fraction of that local day, including DST. Net cost is import plus gas consumption
cost plus standing charges **minus export credit**. Do not add standing charges again
to the net cost returned by the service.

In analytics `totals`, missing consumption coverage makes affected cost components
and net cost null. The supplementary `observed_totals` and each day's
`observed_net_cost_gbp` price only recorded usage plus applicable standing charges;
they do not invent the energy in missing intervals. Missing required rates, unknown
gas units and unsupported tariffs remain null in both representations.

Home Assistant's `cost.*_gbp` fields use **observed** estimated net spend. They may
understate full-period spend when supplier readings are delayed or missing. Partial-day
standing charges are prorated to the request's end, not a promise of the eventual bill.
Check `quality.periods` and `quality.cost_scope` before comparisons. Recorded energy
totals can also be partial: inspect quality, not just `available`.
These estimates exclude supplier account adjustments, bills, credit balances,
payments, rebates and billing reconciliation.

## Supported and unsupported tariffs

Standard single-register published unit and standing rates support fixed/variable
products and time-varying Agile/Go prices, using agreements valid at each interval.
Published Intelligent tariff rates do **not** include intelligent smart-dispatch,
off-peak overrides or session-specific billing adjustments; estimates may differ
from invoices. Economy 7 / `E-2R` and other unimplemented multi-register pricing
must not be silently priced as single-register. No device scheduling/dispatch,
solar self-consumption inference or appliance disaggregation is implemented.

## Quality and habits

Inspect `quality.complete`, `coverage_percent`, `expected_intervals`,
`observed_intervals`, `missing_rate_intervals`, `unknown_gas_unit_meters`,
`unsupported_tariffs` and `warnings`. Expected coverage is based on unique meter
points and agreement windows, rather than blindly multiplying by historical serials.
Missing intervals remain missing, not fabricated zero readings. Daily reports include
days without readings so gaps remain visible. A successful sync does not mean
supplier readings are up to date or every period is complete.

Habits use **electricity import only**: local hour and weekday distributions, with
averages based on calendar occurrences rather than the number of available readings.
`peak_hour` is a local hour (0–23), not a timestamp. `baseload_kw` is the
**duration-weighted 10th percentile of aggregate electricity-import interval-average
power** (`kWh × 3600 / interval seconds`). Only windows with readings covering all
expected import meter points contribute; power across simultaneous meters is summed.
It is a lower-load estimate, not instantaneous load, standby-only consumption or a
measured appliance attribution. In the HA snapshot, habits use the current local
month-to-date. Sparse/zero-consumption readings and corrections can make the estimate
misleading; review the full distribution and coverage before operational decisions.

## Home Assistant history limitations

Cumulative totals are sums of the service's **stored history**, not physical meter
registers or lifetime readings. They can decrease after corrections, changed gas
configuration or history replacement. HA records a polled update at its arrival time,
not at the original supplier interval: delayed readings and corrections therefore
can be attributed to the wrong HA hour/day. The package does not backfill HA external
statistics or precisely reconstruct historical Energy-dashboard attribution. Use the
service's daily analytics for supplier-period analysis, and see
[Home Assistant semantics](home-assistant.md) before selecting Energy sources.
