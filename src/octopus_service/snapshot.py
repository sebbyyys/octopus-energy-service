"""Compact HA snapshot. Costs cover recorded usage, not a bill or live power."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo


def _date(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def _empty(timezone):
    energy = {
        "total_kwh": None,
        "today_kwh": None,
        "yesterday_kwh": None,
        "month_kwh": None,
        "current_rate_pence": None,
    }
    return {
        "available": False,
        "stale": True,
        "last_sync": None,
        "latest_reading": None,
        "currency": "GBP",
        "timezone": timezone,
        "quality": {},
        "electricity": energy,
        "gas": energy.copy(),
        "export": {"total_kwh": None, "today_kwh": None},
        "cost": {
            f"{period}_gbp": None
            for period in ("today", "yesterday", "week", "month", "previous_month")
        },
        "habits": {"peak_hour": None, "baseload_kw": None},
    }


def build_snapshot(store, settings, *, now=None):
    now = (now or datetime.now(UTC)).astimezone(UTC)
    result = _empty(settings.timezone)
    meta = store.get_meta("sync") or {}
    result["last_sync"] = meta.get("last_success")
    meters = store.meters()
    for meter in meters:
        if meter["id"] in settings.gas_units_json:
            meter["gas_unit"] = settings.gas_units_json[meter["id"]]
    _populate_current_rates(result, store, meters, settings, now)
    result["available"] = any(
        result[fuel]["current_rate_pence"] is not None for fuel in ("electricity", "gas")
    )
    result["stale"] = not result["last_sync"] or now - _date(result["last_sync"]) > timedelta(
        seconds=settings.sync_interval_seconds * 3
    )
    readings = store.consumption(datetime(1970, 1, 1, tzinfo=UTC), now)
    if not readings:
        result["quality"] = {"complete": False, "warnings": ["No recorded consumption readings."]}
        return result

    from octopus_service.analytics import analyze

    lookup = {m["id"]: m for m in meters}
    readings = [r for r in readings if r["meter_id"] in lookup]
    if not readings:
        return result
    result["available"] = True
    latest = max(_date(r["interval_end"]) for r in readings)
    result["latest_reading"] = latest.isoformat()
    result["history_from"] = min(_date(r["interval_start"]) for r in readings).isoformat()
    result["stale"] = (
        now - latest > timedelta(hours=48)
        or not result["last_sync"]
        or now - _date(result["last_sync"]) > timedelta(seconds=settings.sync_interval_seconds * 3)
    )

    tz = ZoneInfo(settings.timezone)
    today_date = now.astimezone(tz).date()

    def midnight(date):
        return datetime.combine(date, time.min, tz).astimezone(UTC)

    month_date = today_date.replace(day=1)
    previous_date = (month_date - timedelta(days=1)).replace(day=1)
    periods = {
        "today": (midnight(today_date), now),
        "yesterday": (midnight(today_date - timedelta(days=1)), midnight(today_date)),
        "week": (midnight(today_date - timedelta(days=today_date.weekday())), now),
        "month": (midnight(month_date), now),
        "previous_month": (midnight(previous_date), midnight(month_date)),
    }
    analyses = {}
    for name, (start, end) in periods.items():
        if start == end:
            continue
        relevant = [
            r
            for r in readings
            if _date(r["interval_start"]) < end and _date(r["interval_end"]) > start
        ]
        report = analyze(
            meters,
            relevant,
            store.rates(start, end),
            start,
            end,
            timezone=settings.timezone,
            payment_method=settings.payment_method,
            calorific_value=settings.calorific_value,
            correction_factor=settings.correction_factor,
        )
        analyses[name] = report
        if relevant:
            result["cost"][f"{name}_gbp"] = report.get("observed_totals", report["totals"])[
                "net_cost_gbp"
            ]
        if name in ("today", "yesterday", "month"):
            for fuel, key in (
                ("electricity", "import_kwh"),
                ("gas", "gas_kwh"),
                ("export", "export_kwh"),
            ):
                if fuel == "export" and name != "today":
                    continue
                matching = [r for r in relevant if _fuel(lookup[r["meter_id"]]) == fuel]
                result[fuel][f"{name}_kwh"] = report["totals"][key] if matching else None

    sums, unknown = {}, set()
    for reading in readings:
        meter = lookup[reading["meter_id"]]
        fuel = _fuel(meter)
        value = Decimal(str(reading["consumption"]))
        begin, end = _date(reading["interval_start"]), _date(reading["interval_end"])
        if end > now:
            value *= Decimal(str((now - begin).total_seconds() / (end - begin).total_seconds()))
        if fuel == "gas":
            unit = meter.get("gas_unit", "unknown")
            if unit == "unknown":
                unknown.add(fuel)
                continue
            if unit == "m3":
                value *= (
                    Decimal(str(settings.calorific_value))
                    * Decimal(str(settings.correction_factor))
                    / Decimal("3.6")
                )
        sums[fuel] = sums.get(fuel, Decimal(0)) + value
    for fuel in ("electricity", "gas", "export"):
        result[fuel]["total_kwh"] = (
            float(sums[fuel]) if fuel in sums and fuel not in unknown else None
        )

    if "month" in analyses:
        month = analyses["month"]
        result["quality"] = {
            **month["quality"],
            "periods": {name: report["quality"] for name, report in analyses.items()},
            "cost_scope": "recorded_usage_plus_prorated_standing_charges",
        }
        result["habits"] = {key: month["habits"][key] for key in ("peak_hour", "baseload_kw")}
    return result


def _populate_current_rates(result, store, meters, settings, now):
    current_rates = store.rates(now, now + timedelta(seconds=1))
    for fuel in ("electricity", "gas"):
        values = []
        active = [m for m in meters if m.get("active", True) and _fuel(m) == fuel]
        for meter in active:
            agreements = [
                a
                for a in meter["agreements"]
                if _date(a["valid_from"]) <= now
                and (a["valid_to"] is None or now < _date(a["valid_to"]))
            ]
            candidates = [
                r
                for r in current_rates
                if r["meter_id"] == meter["id"]
                and r["kind"] == "unit"
                and _date(r["valid_from"]) <= now
                and (r["valid_to"] is None or now < _date(r["valid_to"]))
                and any(a["tariff_code"] == r["tariff_code"] for a in agreements)
                and r["payment_method"] in (None, settings.payment_method)
            ]
            if not candidates:
                values.append(None)
            else:
                specific = [r for r in candidates if r["payment_method"] == settings.payment_method]
                rates = {r["value_inc_vat"] for r in (specific or candidates)}
                values.append(next(iter(rates)) if len(rates) == 1 else None)
        if values and None not in values and len(set(values)) == 1:
            result[fuel]["current_rate_pence"] = values[0]


def _fuel(meter):
    return "export" if meter.get("is_export") else meter["fuel"]
