"""Controlled synthetic analytics fixtures, not account data."""

from datetime import UTC, datetime, timedelta

import pytest

from octopus_service.analytics import analyze

START = "2026-01-01T00:00:00+00:00"
END = "2026-01-01T00:30:00+00:00"
TARIFF = "E-1R-TEST-A"


def dt(value):
    return datetime.fromisoformat(value)


def meter(
    serial="one",
    *,
    fuel="electricity",
    point="point",
    export=False,
    gas_unit="unknown",
    agreements=None,
    active=True,
):
    return {
        "id": f"{fuel}:{point}:{serial}",
        "fuel": fuel,
        "meter_point": point,
        "serial_number": serial,
        "is_export": export,
        "gas_unit": gas_unit,
        "active": active,
        "agreements": agreements
        if agreements is not None
        else [{"tariff_code": TARIFF, "valid_from": START, "valid_to": None}],
    }


def reading(m=None, value=1, start=START, end=END):
    return {
        "meter_id": (m or meter())["id"],
        "consumption": value,
        "interval_start": start,
        "interval_end": end,
    }


def rate(m=None, value=20, *, kind="unit", start=START, end=None, tariff=TARIFF, payment=None):
    return {
        "meter_id": (m or meter())["id"],
        "value_inc_vat": value,
        "kind": kind,
        "valid_from": start,
        "valid_to": end,
        "tariff_code": tariff,
        "payment_method": payment,
    }


def run(meters=None, readings=None, rates=None, start=START, end=END, **kwargs):
    return analyze(
        meters if meters is not None else [meter()],
        readings if readings is not None else [reading()],
        rates if rates is not None else [rate(), rate(value=48, kind="standing")],
        dt(start),
        dt(end),
        **kwargs,
    )


@pytest.mark.parametrize("problem", ["unknown_gas", "economy7", "unit_rate", "standing_rate"])
def test_observed_cost_scope_does_not_hide_unpriceable_recorded_usage(problem):
    tariff = "E-2R-TEST-A" if problem == "economy7" else TARIFF
    m = meter(
        fuel="gas" if problem == "unknown_gas" else "electricity",
        agreements=[{"tariff_code": tariff, "valid_from": START, "valid_to": None}],
    )
    prices = []
    if problem != "unit_rate":
        prices.append(rate(m, tariff=tariff))
    if problem != "standing_rate":
        prices.append(rate(m, 48, kind="standing", tariff=tariff))
    result = run([m], [reading(m)], prices, end="2026-01-01T01:00:00+00:00")
    assert result["observed_totals"]["net_cost_gbp"] is None
    assert result["daily"][0]["observed_net_cost_gbp"] is None
    assert result["totals"]["net_cost_gbp"] is None
    assert not result["quality"]["complete"]


@pytest.mark.parametrize(
    "payment, expected_cost", [("DIRECT_DEBIT", 0.25), ("NON_DIRECT_DEBIT", 0.3)]
)
def test_payment_specific_rate_takes_precedence_over_universal_rate(payment, expected_cost):
    result = run(
        rates=[
            rate(value=20),
            rate(value=25, payment="DIRECT_DEBIT"),
            rate(value=30, payment="NON_DIRECT_DEBIT"),
            rate(value=48, kind="standing"),
        ],
        payment_method=payment,
    )
    assert result["totals"]["consumption_cost_gbp"] == expected_cost


def test_expired_temporary_rate_falls_back_to_still_valid_open_rate():
    result = run(
        rates=[
            rate(value=20),
            rate(value=30, start="2026-01-01T00:05:00+00:00", end="2026-01-01T00:20:00+00:00"),
            rate(value=48, kind="standing"),
        ]
    )
    assert result["totals"]["consumption_cost_gbp"] == 0.25
    assert result["quality"]["complete"]


def test_negative_export_credit_increases_net_cost_without_standing():
    export = meter(export=True)
    result = run([export], [reading(export)], [rate(export, -10)])
    assert result["totals"]["export_credit_gbp"] == -0.1
    assert result["totals"]["standing_cost_gbp"] == 0
    assert result["totals"]["net_cost_gbp"] == 0.1
    assert result["quality"]["complete"]


def test_local_hour_buckets_align_after_half_hour_dst_transition():
    start, end = "2026-10-04T02:30:00+11:00", "2026-10-04T04:30:00+11:00"
    result = run(
        readings=[reading(value=2, start=start, end=end)],
        start=start,
        end=end,
        timezone="Australia/Lord_Howe",
    )
    assert result["habits"]["hourly"][2]["kwh"] == 0.5
    assert result["habits"]["hourly"][3]["kwh"] == 1
    assert result["habits"]["hourly"][4]["kwh"] == 0.5


def test_partial_dst_day_standing_uses_actual_25_hour_duration():
    start, end = "2026-10-25T01:00:00+01:00", "2026-10-25T02:00:00+00:00"
    result = run(readings=[reading(start=start, end=end)], start=start, end=end)
    assert result["totals"]["standing_cost_gbp"] == 0.0384
    assert result["quality"]["expected_intervals"] == 4
    assert result["quality"]["complete"]
    assert result["habits"]["hourly"][1]["kwh"] == 1
    assert result["habits"]["hourly"][1]["average_kwh"] == 0.5


def test_partial_day_reports_observed_cost_estimate_with_incomplete_coverage():
    result = run(
        readings=[
            reading(value=0.5, start="2026-01-01T11:00:00+00:00", end="2026-01-01T11:30:00+00:00")
        ],
        rates=[rate(value=25), rate(value=50, kind="standing")],
        end="2026-01-01T12:00:00+00:00",
    )
    assert result["observed_totals"]["consumption_cost_gbp"] == 0.125
    assert result["observed_totals"]["standing_cost_gbp"] == 0.25
    assert result["observed_totals"]["net_cost_gbp"] == 0.375
    assert result["daily"][0]["observed_net_cost_gbp"] == 0.375
    assert result["totals"]["consumption_cost_gbp"] is None
    assert result["totals"]["net_cost_gbp"] is None
    assert result["quality"]["cost_scope"] == "recorded_usage_plus_prorated_standing_charges"
    assert result["quality"]["observed_intervals"] == 1
    assert result["quality"]["expected_intervals"] == 24
    assert not result["quality"]["complete"]
    assert result["quality"]["warnings"]


def test_unobserved_calendar_days_have_quality_warning_even_outside_agreements():
    later = "2026-01-02T00:00:00+00:00"
    stop = "2026-01-02T00:30:00+00:00"
    m = meter(agreements=[{"tariff_code": TARIFF, "valid_from": later, "valid_to": None}])
    result = run(
        [m],
        [reading(m, start=later, end=stop)],
        [rate(m, start=later), rate(m, 48, kind="standing", start=later)],
        end=stop,
    )
    assert result["quality"]["coverage_percent"] == 100
    assert not result["quality"]["complete"]
    assert any("2026-01-01" in warning for warning in result["quality"]["warnings"])


def test_habits_split_midnight_and_average_over_calendar_occurrences():
    start = "2026-01-01T23:45:00+00:00"
    end = "2026-01-02T00:45:00+00:00"
    export = meter(export=True, point="export")
    result = run(
        [meter(), export],
        [reading(value=1, start=start, end=end), reading(export, 99, start=start, end=end)],
        [rate(), rate(value=48, kind="standing"), rate(export)],
        end="2026-01-03T00:00:00+00:00",
    )
    assert result["habits"]["hourly"][23] == {"hour": 23, "kwh": 0.25, "average_kwh": 0.125}
    assert result["habits"]["hourly"][0] == {"hour": 0, "kwh": 0.75, "average_kwh": 0.375}
    assert result["habits"]["weekday"][3]["kwh"] == 0.25
    assert result["habits"]["weekday"][4]["kwh"] == 0.75
    assert result["habits"]["peak_hour"] == 0
    assert result["habits"]["baseload_kw"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        reading(value=-1),
        reading(value=True),
        reading(value=float("nan")),
        reading(value=float("inf")),
        reading(end=START),
        reading(start=END, end=START),
        reading(start="2026-01-01T00:00:00"),
    ],
)
def test_analytics_rejects_invalid_readings(bad):
    with pytest.raises(ValueError):
        run(readings=[bad])


@pytest.mark.parametrize(
    "kwargs", [{"payment_method": "cash"}, {"calorific_value": 0}, {"correction_factor": -1}]
)
def test_analytics_rejects_invalid_billing_options(kwargs):
    with pytest.raises(ValueError):
        run(**kwargs)


@pytest.mark.parametrize("problem", ["unknown_gas", "economy7"])
def test_quality_reports_unsupported_agreements_even_without_readings(problem):
    tariff = "E-2R-TEST-A" if problem == "economy7" else TARIFF
    m = meter(
        fuel="gas" if problem == "unknown_gas" else "electricity",
        agreements=[{"tariff_code": tariff, "valid_from": START, "valid_to": None}],
    )
    result = run([m], [], [rate(m, 48, kind="standing", tariff=tariff)])
    if problem == "unknown_gas":
        assert result["quality"]["unknown_gas_unit_meters"] == [m["id"]]
        assert result["totals"]["gas_kwh"] is None
    else:
        assert result["quality"]["unsupported_tariffs"] == [tariff]
    assert result["totals"]["consumption_cost_gbp"] is None
    assert not result["quality"]["complete"]


@pytest.mark.parametrize("meters", [[], [meter(agreements=[])]])
def test_empty_or_unagreed_data_does_not_fabricate_zero_costs(meters):
    result = run(meters, [], [], end="2026-01-03T00:00:00+00:00")
    assert len(result["daily"]) == 2
    for bucket in [result["totals"], *result["daily"]]:
        assert bucket["gas_kwh"] is None
        for key in (
            "consumption_cost_gbp",
            "standing_cost_gbp",
            "export_credit_gbp",
            "net_cost_gbp",
        ):
            assert bucket[key] is None
    assert result["quality"]["expected_intervals"] == 0
    assert result["quality"]["observed_intervals"] == 0
    assert not result["quality"]["complete"]
    assert result["quality"]["warnings"]
    assert result["habits"]["peak_hour"] is None
    assert result["habits"]["baseload_kw"] is None
    assert all(row["consumption_kwh"] is None for row in result["meter_totals"])


def test_baseload_is_duration_weighted_tenth_percentile_of_total_import_power():
    first, second = meter(), meter(point="second")
    readings = []
    start = dt(START)
    for index in range(20):
        left = (start + timedelta(minutes=30 * index)).isoformat()
        right = (start + timedelta(minutes=30 * (index + 1))).isoformat()
        readings.extend([reading(first, index, left, right), reading(second, 1, left, right)])
    prices = [
        rate(first),
        rate(first, 48, kind="standing"),
        rate(second),
        rate(second, 48, kind="standing"),
    ]
    result = run([first, second], readings, prices, end="2026-01-01T10:00:00+00:00")
    assert result["habits"]["baseload_kw"] == 4


@pytest.mark.parametrize(
    "start, end, slots, repeated_hour",
    [
        ("2026-03-29T00:00:00+00:00", "2026-03-30T00:00:00+01:00", 46, 0),
        ("2026-10-25T00:00:00+01:00", "2026-10-26T00:00:00+00:00", 50, 2),
    ],
)
def test_dst_local_days_coverage_and_hour_occurrence_averages(start, end, slots, repeated_hour):
    left, stop = dt(start).astimezone(UTC), dt(end).astimezone(UTC)
    readings = []
    while left < stop:
        right = left + timedelta(minutes=30)
        readings.append(reading(value=0.5, start=left.isoformat(), end=right.isoformat()))
        left = right
    result = run(readings=readings, start=start, end=end)
    assert len(result["daily"]) == 1
    assert result["daily"][0]["date"] == dt(start).date().isoformat()
    assert result["totals"]["standing_cost_gbp"] == 0.48
    assert result["totals"]["import_kwh"] == slots * 0.5
    assert result["quality"]["expected_intervals"] == slots
    assert result["quality"]["observed_intervals"] == slots
    assert result["quality"]["complete"]
    assert result["habits"]["hourly"][1]["kwh"] == repeated_hour
    assert result["habits"]["hourly"][1]["average_kwh"] == (1 if repeated_hour else 0)
    assert result["habits"]["baseload_kw"] == 1


@pytest.mark.parametrize(
    "omitted, affected, expected_missing",
    [
        ("unit", "consumption_cost_gbp", 1),
        ("standing", "standing_cost_gbp", 1),
        ("both", "net_cost_gbp", 2),
    ],
)
def test_missing_rate_nulls_affected_cost_once_per_reading_or_point_day(
    omitted, affected, expected_missing
):
    prices = [
        rate(value=900, start="2026-01-01T00:15:00+00:00", tariff="E-1R-OTHER-A"),
        rate(value=900, kind="standing", start="2026-01-01T00:15:00+00:00", tariff="E-1R-OTHER-A"),
    ]
    if omitted not in ("unit", "both"):
        prices.append(rate())
    if omitted not in ("standing", "both"):
        prices.append(rate(value=48, kind="standing"))
    result = run(rates=prices)
    assert result["totals"][affected] is None
    assert result["totals"]["net_cost_gbp"] is None
    assert result["quality"]["missing_rate_intervals"] == expected_missing
    assert not result["quality"]["complete"]


def test_missing_readings_preserve_all_days_and_null_incomplete_costs():
    result = run(end="2026-01-03T00:00:00+00:00")
    assert [row["date"] for row in result["daily"]] == ["2026-01-01", "2026-01-02"]
    assert result["daily"][1]["import_kwh"] == 0
    assert result["daily"][1]["consumption_cost_gbp"] is None
    assert result["totals"]["consumption_cost_gbp"] is None
    assert result["totals"]["standing_cost_gbp"] == 0.96
    assert result["totals"]["net_cost_gbp"] is None
    assert result["quality"]["expected_intervals"] == 96
    assert result["quality"]["observed_intervals"] == 1
    assert result["quality"]["coverage_percent"] == pytest.approx(100 / 96)
    assert result["quality"]["missing_rate_intervals"] == 0
    assert not result["quality"]["complete"]
    assert result["quality"]["warnings"]


def test_standing_does_not_fallback_to_obsolete_tariff_after_agreement_change():
    boundary = "2026-01-01T00:15:00+00:00"
    old = meter("old", active=False)
    new = meter(
        "new", agreements=[{"tariff_code": "E-1R-NEXT-A", "valid_from": boundary, "valid_to": None}]
    )
    result = run(
        [old, new],
        [reading(old, 0.5, end=boundary), reading(new, 0.5, start=boundary)],
        [rate(old), rate(old, 48, kind="standing"), rate(new, tariff="E-1R-NEXT-A")],
    )
    assert result["totals"]["standing_cost_gbp"] is None
    assert result["observed_totals"]["standing_cost_gbp"] is None
    assert result["quality"]["missing_rate_intervals"] == 1


def test_overlapping_serial_agreements_never_double_standing_or_coverage():
    old, new = meter("old", active=False), meter("new")
    stop = "2026-01-01T01:00:00+00:00"
    result = run(
        [old, new],
        [reading(old), reading(new, start=END, end=stop)],
        [rate(old), rate(old, 48, kind="standing"), rate(new), rate(new, 48, kind="standing")],
        end=stop,
    )
    assert result["totals"]["standing_cost_gbp"] == 0.02
    assert result["quality"]["expected_intervals"] == 2
    assert result["quality"]["observed_intervals"] == 2
    assert result["quality"]["complete"]


def test_meter_swap_uses_agreement_windows_and_one_meter_point_for_coverage():
    boundary = END
    stop = "2026-01-01T01:00:00+00:00"
    old = meter(
        "old",
        active=False,
        agreements=[{"tariff_code": TARIFF, "valid_from": START, "valid_to": boundary}],
    )
    new = meter(
        "new", agreements=[{"tariff_code": TARIFF, "valid_from": boundary, "valid_to": stop}]
    )
    result = run(
        [old, new],
        [reading(old, 2, end=stop), reading(new, 2, end=stop)],
        [
            rate(old, 20),
            rate(old, 48, kind="standing"),
            rate(new, 40),
            rate(new, 96, kind="standing"),
        ],
        end=stop,
    )
    assert result["totals"]["import_kwh"] == 2
    assert result["totals"]["consumption_cost_gbp"] == 0.6
    assert result["totals"]["standing_cost_gbp"] == 0.03
    assert result["quality"]["expected_intervals"] == 2
    assert result["quality"]["observed_intervals"] == 2
    assert result["quality"]["complete"]
    assert result["habits"]["hourly"][0]["kwh"] == 2
    assert {row["consumption_kwh"] for row in result["meter_totals"]} == {1}


def test_economy7_never_silently_uses_single_register_cost():
    tariff = "E-2R-TEST-A"
    m = meter(agreements=[{"tariff_code": tariff, "valid_from": START, "valid_to": None}])
    result = run(
        [m], [reading(m)], [rate(m, tariff=tariff), rate(m, 48, kind="standing", tariff=tariff)]
    )
    assert result["totals"]["import_kwh"] == 1
    assert result["totals"]["consumption_cost_gbp"] is None
    assert result["totals"]["net_cost_gbp"] is None
    assert result["quality"]["unsupported_tariffs"] == [tariff]
    assert not result["quality"]["complete"]
    assert result["quality"]["warnings"]


def test_unknown_gas_unit_is_null_not_zero_even_with_rates():
    gas = meter(fuel="gas")
    result = run([gas], [reading(gas)], [rate(gas), rate(gas, 48, kind="standing")])
    assert result["totals"]["gas_kwh"] is None
    assert result["totals"]["consumption_cost_gbp"] is None
    assert result["totals"]["standing_cost_gbp"] == 0.01
    assert result["totals"]["net_cost_gbp"] is None
    assert result["meter_totals"] == [{"meter_id": gas["id"], "consumption_kwh": None}]
    assert result["quality"]["unknown_gas_unit_meters"] == [gas["id"]]
    assert result["quality"]["missing_rate_intervals"] == 0
    assert not result["quality"]["complete"]
    assert result["quality"]["warnings"]
    assert result["habits"]["peak_hour"] is None


@pytest.mark.parametrize("gas_unit, raw_gas", [("kwh", 11.135413333333334), ("m3", 1)])
def test_gas_conversion_export_credit_and_negative_prices(gas_unit, raw_gas):
    electric, gas, export = (
        meter(),
        meter(fuel="gas", gas_unit=gas_unit),
        meter(export=True, point="export"),
    )
    prices = [
        rate(electric, -10),
        rate(electric, 48, kind="standing"),
        rate(gas, 5),
        rate(gas, 48, kind="standing"),
        rate(export, 15),
        rate(export, 999, kind="standing"),
    ]
    result = run(
        [electric, gas, export],
        [reading(electric, 2), reading(gas, raw_gas), reading(export, 1)],
        prices,
    )
    assert result["totals"]["import_kwh"] == 2
    assert result["totals"]["export_kwh"] == 1
    assert result["totals"]["gas_kwh"] == pytest.approx(11.135413333333334)
    assert result["totals"]["consumption_cost_gbp"] == pytest.approx(0.3567706666666667)
    assert result["totals"]["standing_cost_gbp"] == 0.02
    assert result["totals"]["export_credit_gbp"] == 0.15
    assert result["totals"]["net_cost_gbp"] == pytest.approx(0.2267706666666667)
    assert result["habits"]["hourly"][0]["kwh"] == 2
    assert result["habits"]["baseload_kw"] == 4
    assert result["quality"]["complete"]


def test_split_rates_agreements_and_round_original_interval_before_clipping():
    boundary = "2026-01-01T00:15:00+00:00"
    m = meter(
        agreements=[
            {"tariff_code": TARIFF, "valid_from": START, "valid_to": boundary},
            {"tariff_code": "E-1R-NEXT-A", "valid_from": boundary, "valid_to": None},
        ]
    )
    prices = [
        rate(m, 20),
        rate(m, 999, payment="NON_DIRECT_DEBIT"),
        rate(m, 30, tariff="E-1R-NEXT-A"),
        rate(m, 40, tariff="E-1R-NEXT-A", payment="DIRECT_DEBIT"),
        rate(m, 48, kind="standing"),
        rate(m, 96, kind="standing", tariff="E-1R-NEXT-A"),
    ]
    result = run([m], [reading(m, 1.015)], prices)
    assert result["totals"]["consumption_cost_gbp"] == 0.306
    assert result["totals"]["standing_cost_gbp"] == 0.015
    clipped = run([m], [reading(m, 1.015)], prices, start=boundary)
    assert clipped["totals"]["import_kwh"] == 0.5075
    assert clipped["totals"]["consumption_cost_gbp"] == 0.204
    varying = run(
        rates=[
            rate(value=10, end=boundary),
            rate(value=-10, start=boundary),
            rate(value=48, kind="standing"),
        ]
    )
    assert varying["totals"]["consumption_cost_gbp"] == 0.0


def test_decimal_half_even_electricity_billing_and_partial_day_standing():
    result = run(readings=[reading(value=1.005)])
    assert result["period_from"] == START
    assert result["period_to"] == END
    assert result["currency"] == "GBP"
    assert result["timezone"] == "Europe/London"
    assert result["totals"] == {
        "import_kwh": 1.005,
        "export_kwh": 0.0,
        "gas_kwh": 0.0,
        "consumption_cost_gbp": 0.2,
        "standing_cost_gbp": 0.01,
        "export_credit_gbp": 0.0,
        "net_cost_gbp": 0.21,
    }
    assert result["daily"] == [
        dict(date="2026-01-01", observed_net_cost_gbp=0.21, **result["totals"])
    ]
    assert result["observed_totals"] == result["totals"]
    assert result["meter_totals"] == [{"meter_id": meter()["id"], "consumption_kwh": 1.005}]
    assert result["quality"] == {
        "cost_scope": "recorded_usage_plus_prorated_standing_charges",
        "complete": True,
        "missing_rate_intervals": 0,
        "unknown_gas_unit_meters": [],
        "unsupported_tariffs": [],
        "expected_intervals": 1,
        "observed_intervals": 1,
        "coverage_percent": 100.0,
        "warnings": [],
    }
    assert len(result["habits"]["hourly"]) == 24
    assert len(result["habits"]["weekday"]) == 7
    assert result["habits"]["peak_hour"] == 0
    assert result["habits"]["hourly"][0] == {"hour": 0, "kwh": 1.005, "average_kwh": 1.005}
    assert result["habits"]["weekday"][3] == {"weekday": 3, "kwh": 1.005, "average_kwh": 1.005}
    assert result["habits"]["baseload_kw"] == 2.01
