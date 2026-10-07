from datetime import UTC, datetime

import pytest

from octopus_service.settings import Settings
from octopus_service.storage import Store


def test_snapshot_with_no_readings_reports_unknown_not_zero(tmp_path):
    from octopus_service.snapshot import build_snapshot

    store = Store(str(tmp_path / "snapshot.sqlite3"))
    try:
        result = build_snapshot(
            store,
            Settings(_env_file=None, service_token="a" * 32),
            now=datetime(2026, 1, 2, 12, tzinfo=UTC),
        )
        assert result["available"] is False
        assert result["electricity"]["total_kwh"] is None
        assert result["cost"]["month_gbp"] is None
        assert result["latest_reading"] is None
    finally:
        store.close()


def test_current_tariff_is_available_without_any_consumption_readings(tmp_path):
    from octopus_service.snapshot import build_snapshot

    store = Store(str(tmp_path / "snapshot.sqlite3"))
    meter_id, tariff = "electricity:1234567890123:ABC", "E-1R-VAR-22-11-01-A"
    store.save_meters(
        [
            {
                "id": meter_id,
                "fuel": "electricity",
                "meter_point": "1234567890123",
                "serial_number": "ABC",
                "is_export": False,
                "active": True,
                "gas_unit": "kwh",
                "agreements": [
                    {"tariff_code": tariff, "valid_from": "2025-01-01T00:00:00Z", "valid_to": None}
                ],
            }
        ]
    )
    store.upsert_rates(
        [
            {
                "meter_id": meter_id,
                "tariff_code": tariff,
                "kind": "unit",
                "valid_from": "2025-01-01T00:00:00Z",
                "valid_to": None,
                "value_inc_vat": 25,
                "payment_method": "DIRECT_DEBIT",
            }
        ]
    )
    store.set_meta("sync", {"last_success": "2026-01-02T11:45:00+00:00"})
    try:
        result = build_snapshot(
            store,
            Settings(_env_file=None, service_token="a" * 32),
            now=datetime(2026, 1, 2, 12, tzinfo=UTC),
        )
        assert result["electricity"]["current_rate_pence"] == 25
        assert result["electricity"]["total_kwh"] is None
        assert result["available"] is True
        assert result["stale"] is False
        assert result["latest_reading"] is None
        assert not result["quality"]["complete"]
    finally:
        store.close()


@pytest.mark.parametrize("fuel", ["electricity", "gas"])
@pytest.mark.parametrize("new_valid_to", [None, "2026-01-02T13:00:00Z"], ids=["open", "bounded"])
@pytest.mark.parametrize(
    ("now", "include_old_rate", "expected_rate"),
    [
        pytest.param(
            datetime(2026, 1, 2, 12, 29, 59, 500000, tzinfo=UTC),
            False,
            None,
            id="future-rate-alone-is-unavailable",
        ),
        pytest.param(
            datetime(2026, 1, 2, 12, 29, 59, 500000, tzinfo=UTC),
            True,
            25,
            id="future-rate-does-not-conflict-with-current",
        ),
        pytest.param(
            datetime(2026, 1, 2, 12, 30, tzinfo=UTC),
            True,
            30,
            id="exact-boundary-excludes-expired-rate",
        ),
    ],
)
def test_current_rate_uses_half_open_validity(
    tmp_path, fuel, new_valid_to, now, include_old_rate, expected_rate
):
    from octopus_service.snapshot import build_snapshot

    store = Store(str(tmp_path / "snapshot.sqlite3"))
    meter_id = f"{fuel}:1234567890123:ABC"
    tariff = f"{'E' if fuel == 'electricity' else 'G'}-1R-VAR-22-11-01-A"
    try:
        store.save_meters(
            [
                {
                    "id": meter_id,
                    "fuel": fuel,
                    "meter_point": "1234567890123",
                    "serial_number": "ABC",
                    "is_export": False,
                    "active": True,
                    "gas_unit": "kwh",
                    "agreements": [
                        {
                            "tariff_code": tariff,
                            "valid_from": "2025-01-01T00:00:00Z",
                            "valid_to": None,
                        }
                    ],
                }
            ]
        )
        rates = [
            {
                "meter_id": meter_id,
                "tariff_code": tariff,
                "kind": "unit",
                "valid_from": "2026-01-02T12:30:00Z",
                "valid_to": new_valid_to,
                "value_inc_vat": 30,
                "payment_method": "DIRECT_DEBIT",
            }
        ]
        if include_old_rate:
            rates.append(
                {
                    "meter_id": meter_id,
                    "tariff_code": tariff,
                    "kind": "unit",
                    "valid_from": "2026-01-02T12:00:00Z",
                    "valid_to": "2026-01-02T12:30:00Z",
                    "value_inc_vat": 25,
                    "payment_method": "DIRECT_DEBIT",
                }
            )
        store.upsert_rates(rates)

        result = build_snapshot(
            store,
            Settings(_env_file=None, service_token="a" * 32),
            now=now,
        )

        assert result[fuel]["current_rate_pence"] == expected_rate
        assert result["available"] is (expected_rate is not None)
        assert result["latest_reading"] is None
    finally:
        store.close()


def test_snapshot_exposes_observed_energy_cost_current_rate_and_freshness(tmp_path):
    from octopus_service.snapshot import build_snapshot

    store = Store(str(tmp_path / "snapshot.sqlite3"))
    meter_id = "electricity:1234567890123:ABC"
    tariff = "E-1R-VAR-22-11-01-A"
    store.save_meters(
        [
            {
                "id": meter_id,
                "fuel": "electricity",
                "meter_point": "1234567890123",
                "serial_number": "ABC",
                "is_export": False,
                "active": True,
                "gas_unit": "kwh",
                "agreements": [
                    {"tariff_code": tariff, "valid_from": "2025-01-01T00:00:00Z", "valid_to": None}
                ],
            }
        ]
    )
    store.upsert_consumption(
        [
            {
                "meter_id": meter_id,
                "interval_start": "2026-01-02T11:00:00Z",
                "interval_end": "2026-01-02T11:30:00Z",
                "consumption": 0.5,
            }
        ]
    )
    store.upsert_rates(
        [
            {
                "meter_id": meter_id,
                "tariff_code": tariff,
                "kind": kind,
                "valid_from": "2025-01-01T00:00:00Z",
                "valid_to": None,
                "value_inc_vat": value,
                "payment_method": "DIRECT_DEBIT",
            }
            for kind, value in (("unit", 25), ("standing", 50))
        ]
    )
    store.set_meta("sync", {"last_success": "2026-01-02T11:45:00+00:00"})
    try:
        result = build_snapshot(
            store,
            Settings(_env_file=None, service_token="a" * 32),
            now=datetime(2026, 1, 2, 12, tzinfo=UTC),
        )
        assert result["available"] is True
        assert result["stale"] is False
        assert result["electricity"]["total_kwh"] == 0.5
        assert result["electricity"]["today_kwh"] == 0.5
        assert result["electricity"]["current_rate_pence"] == 25
        assert result["electricity"]["yesterday_kwh"] is None
        assert result["gas"]["total_kwh"] is None
        assert result["cost"]["today_gbp"] == 0.375
        assert result["quality"]["complete"] is False
    finally:
        store.close()
