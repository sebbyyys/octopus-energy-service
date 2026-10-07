"""Decimal estimated billing and local-calendar analytics (not a supplier invoice)."""

from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

ZERO = Decimal(0)
HUNDRED = Decimal(100)
ENERGY_KEYS = ("import_kwh", "export_kwh", "gas_kwh")
COST_KEYS = ("consumption_cost_gbp", "standing_cost_gbp", "export_credit_gbp")


def _dt(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _seconds(start, end):
    delta = end - start
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000


def _number(value):
    if isinstance(value, bool):
        raise ValueError("numbers must not be booleans")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid numeric value") from exc
    if not result.is_finite():
        raise ValueError("numbers must be finite")
    return result


def _merge_ranges(ranges):
    merged = []
    for left, right in sorted(ranges):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def _point(meter):
    return meter["fuel"], meter["meter_point"], meter["is_export"]


def _agreements_at(meter, instant):
    return [
        a
        for a in meter["agreements"]
        if _dt(a["valid_from"]) <= instant
        and (a.get("valid_to") is None or instant < _dt(a["valid_to"]))
    ]


def _slots(ranges):
    slots = {}
    for left, right in _merge_ranges(ranges):
        slot = left.replace(minute=(left.minute // 30) * 30, second=0, microsecond=0)
        while slot < right:
            slots.setdefault(slot, []).append(
                (max(left, slot), min(right, slot + timedelta(minutes=30)))
            )
            slot += timedelta(minutes=30)
    return slots


def _baseload(power_events, expected_windows, observed_windows):
    events = {}
    for instant, power in power_events:
        events.setdefault(instant, [ZERO, 0, 0])[0] += power
    for windows, index in ((expected_windows, 1), (observed_windows, 2)):
        for point, ranges in windows.items():
            if point[0] != "electricity" or point[2]:
                continue
            for left, right in _merge_ranges(ranges):
                events.setdefault(left, [ZERO, 0, 0])[index] += 1
                events.setdefault(right, [ZERO, 0, 0])[index] -= 1
    powers = []
    current_power, wanted, seen = ZERO, 0, 0
    instants = sorted(events)
    for index, instant in enumerate(instants[:-1]):
        delta_power, delta_wanted, delta_seen = events[instant]
        current_power += delta_power
        wanted += delta_wanted
        seen += delta_seen
        if wanted > 0 and wanted == seen:
            powers.append((current_power, _seconds(instant, instants[index + 1])))
    threshold = sum((duration for _, duration in powers), ZERO) / 10
    elapsed = ZERO
    for power, duration in sorted(powers):
        elapsed += duration
        if elapsed >= threshold:
            return float(power)
    return None


class _RateIndex:
    """Index tariff/payment histories without scanning every rate for each reading."""

    def __init__(self, rates, payment_method):
        self.groups = {}
        self.boundaries = {}
        for sequence, rate in enumerate(rates):
            if rate.get("payment_method") not in (None, payment_method):
                continue
            left = _dt(rate["valid_from"])
            right = (
                _dt(rate["valid_to"]) if rate.get("valid_to") else datetime.max.replace(tzinfo=UTC)
            )
            key = (rate["meter_id"], rate["kind"], rate["tariff_code"], rate.get("payment_method"))
            self.groups.setdefault(key, []).append(
                (left, sequence, right, _number(rate["value_inc_vat"]))
            )
            self.boundaries.setdefault(key[:2], set()).update((left, right))
        for key, entries in self.groups.items():
            entries.sort()
            starts = [entry[0] for entry in entries]
            maximum = datetime.min.replace(tzinfo=UTC)
            latest_end = []
            for entry in entries:
                maximum = max(maximum, entry[2])
                latest_end.append(maximum)
            self.groups[key] = (entries, starts, latest_end)
        self.payment_method = payment_method

    def selected(self, meter, instant, kind):
        candidates = []
        for agreement in _agreements_at(meter, instant):
            for payment in (None, self.payment_method):
                key = (meter["id"], kind, agreement["tariff_code"], payment)
                group = self.groups.get(key)
                if group is None:
                    continue
                entries, starts, latest_end = group
                index = bisect_right(starts, instant) - 1
                while index >= 0 and latest_end[index] > instant:
                    left, sequence, right, value = entries[index]
                    if right > instant:
                        candidates.append((payment is not None, left, sequence, value))
                        break
                    index -= 1
        return max(candidates)[3] if candidates else None


def _overlapping(items, starts, left, right):
    return items[max(0, bisect_right(starts, left) - 1) : bisect_left(starts, right)]


def _empty():
    return dict.fromkeys((*ENERGY_KEYS, *COST_KEYS), ZERO)


def _sum_buckets(buckets):
    result = _empty()
    for key in result:
        values = [bucket[key] for bucket in buckets]
        result[key] = sum(values, ZERO) if all(value is not None for value in values) else None
    return result


def _serialize(bucket):
    result = {key: float(value) if value is not None else None for key, value in bucket.items()}
    costs = [bucket[key] for key in COST_KEYS]
    result["net_cost_gbp"] = (
        float(costs[0] + costs[1] - costs[2]) if all(value is not None for value in costs) else None
    )
    return result


def _calendar(start, end, zone):
    days, hours = [], []
    date = start.astimezone(zone).date()
    while True:
        midnight = datetime.combine(date, time(), zone).astimezone(UTC)
        tomorrow = datetime.combine(date + timedelta(days=1), time(), zone).astimezone(UTC)
        if midnight >= end:
            break
        lo, hi = max(start, midnight), min(end, tomorrow)
        days.append((date, lo, hi, midnight, tomorrow))
        cursor = midnight
        while cursor < tomorrow:
            local = cursor.astimezone(zone)
            to_hour = timedelta(hours=1) - timedelta(
                minutes=local.minute, seconds=local.second, microseconds=local.microsecond
            )
            next_hour = min(cursor + to_hour, tomorrow)
            left, right = max(start, cursor), min(end, next_hour)
            if left < right:
                hours.append((left, right, cursor.astimezone(zone).hour, date.weekday()))
            cursor = next_hour
        date += timedelta(days=1)
    return days, hours


def analyze(
    meters,
    consumption,
    rates,
    start,
    end,
    *,
    timezone="Europe/London",
    payment_method="DIRECT_DEBIT",
    calorific_value=39.2,
    correction_factor=1.02264,
):
    start, end = _dt(start), _dt(end)
    if end <= start:
        raise ValueError("period duration must be positive")
    if payment_method not in ("DIRECT_DEBIT", "NON_DIRECT_DEBIT"):
        raise ValueError("invalid payment method")
    calorific_value, correction_factor = _number(calorific_value), _number(correction_factor)
    if calorific_value <= 0 or correction_factor <= 0:
        raise ValueError("gas conversion parameters must be positive")
    zone = ZoneInfo(timezone)
    days, hours = _calendar(start, end, zone)
    daily = {date.isoformat(): _empty() for date, *_ in days}
    day_starts = [day[1] for day in days]
    hour_starts = [hour[0] for hour in hours]
    by_id = {meter["id"]: meter for meter in meters}
    meter_energy = dict.fromkeys(by_id)
    hourly_energy, weekday_energy = [ZERO] * 24, [ZERO] * 7
    power_events = []
    observed_dates = set()
    unknown_gas = set()
    unsupported = set()
    point_windows = {}
    observed_windows = {}
    for meter in meters:
        point_windows.setdefault(_point(meter), [])
        observed_windows.setdefault(_point(meter), [])
        for agreement in meter["agreements"]:
            left = max(start, _dt(agreement["valid_from"]))
            right = min(end, _dt(agreement["valid_to"]) if agreement.get("valid_to") else end)
            if left < right:
                point_windows[_point(meter)].append((left, right))
                unknown = meter["fuel"] == "gas" and meter.get("gas_unit", "unknown") == "unknown"
                invalid = agreement["tariff_code"].startswith("E-2R-")
                if unknown:
                    unknown_gas.add(meter["id"])
                if invalid:
                    unsupported.add(agreement["tariff_code"])
                if unknown or invalid:
                    for date, *_ in _overlapping(days, day_starts, left, right):
                        bucket = daily[date.isoformat()]
                        bucket[
                            "export_credit_gbp" if meter["is_export"] else "consumption_cost_gbp"
                        ] = None
                        if unknown:
                            bucket["gas_kwh"] = None

    expected = 0
    observed = 0
    missing_units = set()
    missing_standing = set()

    rate_index = _RateIndex(rates, payment_method)
    selected_rate = rate_index.selected
    cuts = {}
    for meter in meters:
        agreement_cuts = {
            _dt(a[key])
            for a in meter["agreements"]
            for key in ("valid_from", "valid_to")
            if a.get(key) is not None
        }
        for kind in ("unit", "standing"):
            cuts[meter["id"], kind] = sorted(
                agreement_cuts | rate_index.boundaries.get((meter["id"], kind), set())
            )

    def segments(meter, left, right, kind):
        available = cuts[meter["id"], kind]
        boundaries = [
            left,
            *available[bisect_right(available, left) : bisect_left(available, right)],
            right,
        ]
        return zip(boundaries[:-1], boundaries[1:], strict=True)

    for record in consumption:
        meter = by_id.get(record["meter_id"])
        if meter is None:
            continue
        original_start, original_end = _dt(record["interval_start"]), _dt(record["interval_end"])
        raw = _number(record["consumption"])
        if original_end <= original_start or raw < 0:
            raise ValueError("readings need positive duration and nonnegative consumption")
        left, right = max(start, original_start), min(end, original_end)
        if right <= left:
            continue
        if meter["fuel"] == "gas" and meter["gas_unit"] == "m3":
            raw *= _number(correction_factor) * _number(calorific_value) / Decimal("3.6")
        is_import = meter["fuel"] == "electricity" and not meter["is_export"]
        energy_key = (
            "gas_kwh"
            if meter["fuel"] == "gas"
            else "export_kwh"
            if meter["is_export"]
            else "import_kwh"
        )
        cost_key = "export_credit_gbp" if meter["is_export"] else "consumption_cost_gbp"
        billed = (
            raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            if meter["fuel"] == "electricity"
            else raw
        )
        for date, day_start, day_end, *_ in _overlapping(days, day_starts, left, right):
            lo, hi = max(left, day_start), min(right, day_end)
            if lo >= hi:
                continue
            bucket = daily[date.isoformat()]
            for segment_start, segment_end in segments(meter, lo, hi, "unit"):
                if not _agreements_at(meter, segment_start):
                    continue
                observed_windows[_point(meter)].append((segment_start, segment_end))
                observed_dates.add(date.isoformat())
                fraction = _seconds(segment_start, segment_end) / _seconds(
                    original_start, original_end
                )
                energy = raw * fraction
                if meter["fuel"] == "gas" and meter["gas_unit"] == "unknown":
                    unknown_gas.add(meter["id"])
                    bucket[energy_key] = None
                    bucket[cost_key] = None
                    meter_energy[meter["id"]] = None
                    continue
                if bucket[energy_key] is not None:
                    bucket[energy_key] += energy
                meter_energy[meter["id"]] = (meter_energy[meter["id"]] or ZERO) + energy
                invalid = [
                    a["tariff_code"]
                    for a in meter["agreements"]
                    if a["tariff_code"].startswith("E-2R-")
                    and _dt(a["valid_from"]) <= segment_start
                    and (a.get("valid_to") is None or segment_start < _dt(a["valid_to"]))
                ]
                if invalid:
                    unsupported.update(invalid)
                    bucket[cost_key] = None
                    continue
                unit = selected_rate(meter, segment_start, "unit")
                if unit is None:
                    bucket[cost_key] = None
                    missing_units.add((meter["id"], original_start, original_end))
                elif bucket[cost_key] is not None:
                    bucket[cost_key] += billed * fraction * unit / HUNDRED
        if is_import:
            for segment_start, segment_end in segments(meter, left, right, "unit"):
                if not _agreements_at(meter, segment_start):
                    continue
                power = raw * 3600 / _seconds(original_start, original_end)
                power_events.extend(((segment_start, power), (segment_end, -power)))
                for hour_start, hour_end, hour, weekday in _overlapping(
                    hours, hour_starts, segment_start, segment_end
                ):
                    lo, hi = max(segment_start, hour_start), min(segment_end, hour_end)
                    if lo < hi:
                        energy = raw * _seconds(lo, hi) / _seconds(original_start, original_end)
                        hourly_energy[hour] += energy
                        weekday_energy[weekday] += energy

    standing_points = {}
    for meter in meters:
        if not meter["is_export"]:
            standing_points.setdefault(_point(meter), []).append(meter)
    for point, point_meters in standing_points.items():
        for date, lo, hi, midnight, tomorrow in days:
            bucket = daily[date.isoformat()]
            boundaries = {lo, hi}
            for meter in point_meters:
                for left, right in segments(meter, lo, hi, "standing"):
                    boundaries.update((left, right))
            boundaries = sorted(boundaries)
            for segment_start, segment_end in zip(boundaries[:-1], boundaries[1:], strict=True):
                candidates = [
                    meter for meter in point_meters if _agreements_at(meter, segment_start)
                ]
                if not candidates:
                    continue
                candidates.sort(
                    key=lambda meter: (
                        max(_dt(a["valid_from"]) for a in _agreements_at(meter, segment_start)),
                        meter.get("active", True),
                        meter["id"],
                    ),
                    reverse=True,
                )
                # The newest applicable agreement owns this meter point's charge.
                # A missing current rate must not fall back to an obsolete serial/tariff.
                standing = selected_rate(candidates[0], segment_start, "standing")
                if standing is None:
                    bucket["standing_cost_gbp"] = None
                    missing_standing.add((point, date))
                elif bucket["standing_cost_gbp"] is not None:
                    bucket["standing_cost_gbp"] += (
                        standing
                        * _seconds(segment_start, segment_end)
                        / _seconds(midnight, tomorrow)
                        / HUNDRED
                    )

    for date, lo, hi, *_ in days:
        if not any(
            left < hi and right > lo
            for windows in point_windows.values()
            for left, right in windows
        ):
            bucket = daily[date.isoformat()]
            for key in (*COST_KEYS, "gas_kwh"):
                bucket[key] = None

    observed_totals = _serialize(_sum_buckets(list(daily.values())))
    observed_daily_costs = {
        date: _serialize(bucket)["net_cost_gbp"] for date, bucket in daily.items()
    }

    for point, windows in point_windows.items():
        wanted = _slots(windows)
        seen = _slots(observed_windows[point])
        expected += len(wanted)
        for slot, portions in wanted.items():
            covered = all(
                any(a <= left and b >= right for a, b in seen.get(slot, []))
                for left, right in portions
            )
            if covered:
                observed += 1
            else:
                cost_key = "export_credit_gbp" if point[2] else "consumption_cost_gbp"
                for date, lo, hi, *_ in _overlapping(
                    days, day_starts, portions[0][0], portions[-1][1]
                ):
                    if any(left < hi and right > lo for left, right in portions):
                        daily[date.isoformat()][cost_key] = None
        seen_ranges = _merge_ranges(observed_windows[point])
        for date, lo, hi, *_ in days:
            if any(left < hi and right > lo for left, right in windows) and not any(
                left < hi and right > lo for left, right in seen_ranges
            ):
                cost_key = "export_credit_gbp" if point[2] else "consumption_cost_gbp"
                daily[date.isoformat()][cost_key] = None
                if point[0] == "gas":
                    daily[date.isoformat()]["gas_kwh"] = None

    totals = _sum_buckets(list(daily.values()))
    hour_counts = Counter(hour for _, _, hour, _ in hours)
    weekday_counts = Counter(date.weekday() for date, *_ in days)
    missing = len(missing_units) + len(missing_standing)
    warnings = []
    if not expected:
        warnings.append("No meter agreements overlap this period.")
    if observed < expected:
        warnings.append(f"Incomplete consumption coverage: {observed}/{expected} half-hour slots.")
    if missing:
        warnings.append(
            f"Missing rate coverage: {len(missing_units)} readings and {len(missing_standing)} meter-point days."
        )
    if unknown_gas:
        warnings.append("Gas units are unknown: " + ", ".join(sorted(unknown_gas)))
    if unsupported:
        warnings.append("Unsupported tariffs: " + ", ".join(sorted(unsupported)))
    absent_dates = sorted(set(daily) - observed_dates)
    if absent_dates:
        warnings.append("No readings on local dates: " + ", ".join(absent_dates))
    complete = not warnings
    return {
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
        "currency": "GBP",
        "timezone": timezone,
        "totals": _serialize(totals),
        "observed_totals": observed_totals,
        "daily": [
            dict(date=date, observed_net_cost_gbp=observed_daily_costs[date], **_serialize(bucket))
            for date, bucket in daily.items()
        ],
        "meter_totals": [
            {"meter_id": key, "consumption_kwh": float(value) if value is not None else None}
            for key, value in sorted(meter_energy.items())
        ],
        "habits": {
            "hourly": [
                {
                    "hour": hour,
                    "kwh": float(energy),
                    "average_kwh": float(energy / hour_counts[hour]) if hour_counts[hour] else 0.0,
                }
                for hour, energy in enumerate(hourly_energy)
            ],
            "weekday": [
                {
                    "weekday": weekday,
                    "kwh": float(energy),
                    "average_kwh": float(energy / weekday_counts[weekday])
                    if weekday_counts[weekday]
                    else 0.0,
                }
                for weekday, energy in enumerate(weekday_energy)
            ],
            "peak_hour": max(range(24), key=lambda h: hourly_energy[h]) if power_events else None,
            "baseload_kw": _baseload(power_events, point_windows, observed_windows),
        },
        "quality": {
            "cost_scope": "recorded_usage_plus_prorated_standing_charges",
            "complete": complete,
            "missing_rate_intervals": missing,
            "unknown_gas_unit_meters": sorted(unknown_gas),
            "unsupported_tariffs": sorted(unsupported),
            "expected_intervals": expected,
            "observed_intervals": observed,
            "coverage_percent": float(Decimal(observed) / expected * HUNDRED) if expected else 0.0,
            "warnings": warnings,
        },
    }
