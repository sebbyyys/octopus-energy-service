import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from octopus_service.storage import Store


def dt(value):
    return datetime.fromisoformat(value)


def meter(serial="one"):
    return {
        "id": f"electricity:point:{serial}",
        "fuel": "electricity",
        "meter_point": "point",
        "serial_number": serial,
        "is_export": False,
        "gas_unit": "unknown",
        "active": True,
        "agreements": [],
    }


def reading(value=1, start="2026-01-01T00:00:00+00:00", end="2026-01-01T00:30:00+00:00"):
    return {
        "meter_id": meter()["id"],
        "interval_start": start,
        "interval_end": end,
        "consumption": value,
    }


def test_store_creates_parent_and_roundtrips_meters_with_wal(tmp_path):
    path = tmp_path / "nested" / "data.sqlite3"
    store = Store(str(path))
    store.save_meters([meter("z"), meter("a")])
    assert [m["serial_number"] for m in store.meters()] == ["a", "z"]
    db = sqlite3.connect(path)
    try:
        assert db.execute("pragma journal_mode").fetchone()[0] == "wal"
    finally:
        db.close()
    store.close()


@pytest.mark.parametrize(
    "bad",
    [
        reading(-1),
        reading(float("nan")),
        reading(float("inf")),
        reading(start="2026-01-01T00:00:00"),
        reading(end="2026-01-01T00:00:00+00:00"),
    ],
)
def test_invalid_consumption_rolls_back_entire_batch(tmp_path, bad):
    store = Store(str(tmp_path / "db"))
    try:
        with pytest.raises(ValueError):
            store.upsert_consumption([reading(), bad])
        assert (
            store.consumption(dt("2026-01-01T00:00:00+00:00"), dt("2026-01-02T00:00:00+00:00"))
            == []
        )
    finally:
        store.close()


def test_meter_refresh_retains_inactive_history_and_atomic_metadata(tmp_path):
    store = Store(str(tmp_path / "db"))
    try:
        store.save_meters([meter("old")])
        store.save_meters([meter("new")])
        assert {m["serial_number"]: m["active"] for m in store.meters()} == {
            "new": True,
            "old": False,
        }
        store.set_meta("sync'; DROP TABLE meters; --", {"count": 1})
        assert store.get_meta("sync'; DROP TABLE meters; --") == {"count": 1}
        assert store.get_meta("absent") is None
        store.save_meters([meter("old")])
        assert next(m for m in store.meters() if m["serial_number"] == "old")["active"]
        with pytest.raises(ValueError):
            store.save_meters(
                [
                    meter("z"),
                    dict(
                        meter("a"),
                        agreements=[
                            {
                                "tariff_code": "E-1R-X-A",
                                "valid_from": "2026-01-01",
                                "valid_to": None,
                            }
                        ],
                    ),
                ]
            )
        assert not any(m["serial_number"] == "z" for m in store.meters())
    finally:
        store.close()


def test_rates_identity_includes_payment_and_updates_open_end(tmp_path):
    store = Store(str(tmp_path / "db"))
    rate = {
        "meter_id": meter()["id"],
        "tariff_code": "E-1R-P-REG",
        "kind": "unit",
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to": None,
        "value_inc_vat": -4,
        "payment_method": None,
    }
    try:
        assert (
            store.upsert_rates([rate, dict(rate, payment_method="DIRECT_DEBIT", value_inc_vat=5)])
            == 2
        )
        assert store.upsert_rates([dict(rate, value_inc_vat=-3)]) == 0
        rows = store.rates(dt("2026-01-02T00:00:00+00:00"), dt("2026-01-03T00:00:00+00:00"))
        assert len(rows) == 2
        assert next(r for r in rows if r["payment_method"] is None)["value_inc_vat"] == -3
        assert (
            store.rates(dt("2026-01-02T00:00:00+00:00"), dt("2026-01-03T00:00:00+00:00"), "other")
            == []
        )
        with pytest.raises(ValueError):
            store.upsert_rates(
                [dict(rate, value_inc_vat=99), dict(rate, value_inc_vat=float("nan"))]
            )
        assert (
            next(
                r
                for r in store.rates(
                    dt("2026-01-02T00:00:00+00:00"), dt("2026-01-03T00:00:00+00:00")
                )
                if r["payment_method"] is None
            )["value_inc_vat"]
            == -3
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"fuel": "oil"},
        {"gas_unit": "ft3"},
        {"active": "yes"},
        {"is_export": 1},
        {"meter_point": ""},
        {"serial_number": ""},
        {"id": ""},
    ],
)
def test_invalid_meter_refresh_is_atomic(tmp_path, change):
    store = Store(str(tmp_path / "db"))
    try:
        store.save_meters([meter()])
        with pytest.raises(ValueError):
            store.save_meters([dict(meter("new"), **change)])
        assert store.meters() == [meter()]
    finally:
        store.close()


@pytest.mark.parametrize(
    "field, value",
    [("meter_id", None), ("meter_id", ""), ("tariff_code", ""), ("tariff_code", None)],
)
def test_invalid_record_identifiers_do_not_enter_ledger(tmp_path, field, value):
    store = Store(str(tmp_path / "db"))
    price = {
        "meter_id": meter()["id"],
        "tariff_code": "E-1R-TEST-A",
        "kind": "unit",
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to": None,
        "value_inc_vat": 20,
        "payment_method": None,
    }
    try:
        with pytest.raises(ValueError):
            store.upsert_rates([dict(price, **{field: value})])
        if field == "meter_id":
            with pytest.raises(ValueError):
                store.upsert_consumption([dict(reading(), meter_id=value)])
    finally:
        store.close()


@pytest.mark.parametrize('bad', [
    {'valid_to': '2026-01-01T00:00:00+00:00'}, {'kind': 'night'},
    {'payment_method': 'CASH'}, {'value_inc_vat': True},
])
def test_invalid_rates_are_atomic(tmp_path, bad):
    store = Store(str(tmp_path / 'db'))
    price = {'meter_id': meter()['id'], 'tariff_code': 'E-1R-TEST-A', 'kind': 'unit',
             'valid_from': '2026-01-01T00:00:00+00:00', 'valid_to': None,
             'value_inc_vat': 20, 'payment_method': None}
    try:
        with pytest.raises(ValueError):
            store.upsert_rates([price, dict(price, **bad)])
        assert store.rates(dt('2026-01-01T00:00:00+00:00'), dt('2026-01-02T00:00:00+00:00')) == []
    finally:
        store.close()


def test_meter_agreements_normalize_utc_and_reject_nonpositive_duration(tmp_path):
    store = Store(str(tmp_path / 'db'))
    agreement = {'tariff_code': 'E-1R-TEST-A', 'valid_from': '2026-01-01T01:00:00+01:00',
                 'valid_to': '2026-01-02T01:00:00+01:00'}
    try:
        store.save_meters([dict(meter(), agreements=[agreement])])
        stored = store.meters()[0]['agreements'][0]
        assert stored['valid_from'] == '2026-01-01T00:00:00+00:00'
        assert stored['valid_to'] == '2026-01-02T00:00:00+00:00'
        with pytest.raises(ValueError):
            store.save_meters([dict(meter(), agreements=[dict(agreement, valid_to=agreement['valid_from'])])])
        assert store.meters()[0]['agreements'][0] == stored
        with pytest.raises(ValueError):
            store.set_meta('bad', [])
    finally:
        store.close()


def test_same_store_handles_concurrent_thread_writes(tmp_path):
    store = Store(str(tmp_path / 'db'))
    try:
        def insert(index):
            item = dict(reading(index), meter_id=f'gas:point:{index}')
            store.upsert_consumption([item])
            return store.consumption_bounds()['count']
        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = list(pool.map(insert, range(32)))
        assert max(counts) == 32
        assert store.consumption_bounds()['count'] == 32
        assert all(r['consumption'] >= 0 for r in store.consumption(
            dt('2026-01-01T00:00:00+00:00'), dt('2026-01-02T00:00:00+00:00')))
    finally:
        store.close()


def test_consumption_bounds_cover_stored_history_and_optional_meter_filter(tmp_path):
    store = Store(str(tmp_path / "db"))
    try:
        assert store.consumption_bounds() == {"start": None, "end": None, "count": 0}
        records = [
            reading(),
            dict(
                reading(start="2026-01-02T00:00:00+00:00", end="2026-01-02T00:30:00+00:00"),
                meter_id="gas:other:one",
            ),
        ]
        store.upsert_consumption(records)
        assert store.consumption_bounds() == {
            "start": records[0]["interval_start"],
            "end": records[1]["interval_end"],
            "count": 2,
        }
        assert store.consumption_bounds(meter()["id"]) == {
            "start": records[0]["interval_start"],
            "end": records[0]["interval_end"],
            "count": 1,
        }
        assert store.consumption_bounds("'; DROP TABLE consumption; --")["count"] == 0
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["rates", "metadata"])
def test_apply_sync_commits_snapshot_or_rolls_back_every_table(tmp_path, failure):
    path = str(tmp_path / "db")
    store = Store(path)
    lo, hi = dt("2026-01-01T00:00:00+00:00"), dt("2026-01-02T00:00:00+00:00")
    price = {
        "meter_id": meter()["id"],
        "tariff_code": "E-1R-TEST-A",
        "kind": "unit",
        "valid_from": lo.isoformat(),
        "valid_to": None,
        "value_inc_vat": 20,
        "payment_method": None,
    }
    try:
        store.apply_sync([meter()], [reading()], [price], {"last_success": "initial"})
        with pytest.raises(ValueError):
            store.apply_sync(
                [meter("new")],
                [reading(2)],
                [dict(price, value_inc_vat=float("nan") if failure == "rates" else 99)],
                {"last_success": float("nan") if failure == "metadata" else "failed"},
            )
        assert store.meters() == [meter()]
        assert store.consumption(lo, hi) == [reading()]
        assert store.rates(lo, hi) == [price]
        assert store.get_meta("sync") == {"last_success": "initial"}
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert reopened.consumption(lo, hi) == [reading()]
        assert reopened.get_meta("sync") == {"last_success": "initial"}
    finally:
        reopened.close()


def test_consumption_upsert_corrections_and_overlap_are_utc(tmp_path):
    store = Store(str(tmp_path / "db"))
    try:
        assert (
            store.upsert_consumption(
                [reading(start="2026-01-01T01:00:00+01:00", end="2026-01-01T01:30:00+01:00")]
            )
            == 1
        )
        assert store.upsert_consumption([reading(2)]) == 0
        assert store.upsert_consumption([reading(2)]) == 0
        rows = store.consumption(dt("2026-01-01T00:10:00+00:00"), dt("2026-01-01T00:20:00+00:00"))
        assert rows == [reading(2)]
        assert (
            store.consumption(dt("2026-01-01T00:30:00+00:00"), dt("2026-01-01T01:00:00+00:00"))
            == []
        )
        assert (
            store.consumption(
                dt("2026-01-01T00:00:00+00:00"), dt("2026-01-02T00:00:00+00:00"), "other"
            )
            == []
        )
    finally:
        store.close()
