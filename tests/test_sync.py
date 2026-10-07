import asyncio
from datetime import UTC, datetime, timedelta

import pytest


class MemoryStore:
    def __init__(self):
        self.metadata = {}
        self.readings = []
        self.prices = []
        self.meter_list = []

    def get_meta(self, key):
        return self.metadata.get(key)

    def set_meta(self, key, value):
        self.metadata[key] = value

    def save_meters(self, value):
        self.meter_list = value

    def meters(self):
        return self.meter_list

    def upsert_consumption(self, value):
        self.readings.extend(value)
        return len(value)

    def upsert_rates(self, value):
        self.prices.extend(value)
        return len(value)


@pytest.mark.asyncio
async def test_sync_without_credentials_does_not_make_network_calls():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    store = MemoryStore()
    manager = SyncManager(store, Settings(_env_file=None, service_token="a" * 32))
    with pytest.raises(ValueError, match="credentials"):
        await manager.synchronize(
            datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )
    assert manager.running is False
    assert store.readings == []


class FakeClient:
    calls = []
    rate_calls = []

    def __init__(self, api_key):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def account(self, account_number):
        return {
            "properties": [
                {
                    "moved_out_at": None,
                    "electricity_meter_points": [
                        {
                            "mpan": "1234567890123",
                            "is_export": False,
                            "meters": [{"serial_number": "ABC"}],
                            "agreements": [
                                {
                                    "tariff_code": "E-1R-AGILE-24-10-01-A",
                                    "valid_from": "2025-01-01T00:00:00Z",
                                    "valid_to": None,
                                }
                            ],
                        }
                    ],
                    "gas_meter_points": [],
                }
            ]
        }

    async def consumption(self, meter, start, end):
        self.calls.append((start, end))
        return [
            {
                "meter_id": meter["id"],
                "interval_start": start.isoformat(),
                "interval_end": (start.replace(minute=30)).isoformat(),
                "consumption": 0.5,
            }
        ]

    async def rates(self, meter, tariff_code, kind, start, end, payment_method="DIRECT_DEBIT"):
        self.rate_calls.append((tariff_code, start, end))
        return [
            {
                "meter_id": meter["id"],
                "tariff_code": tariff_code,
                "kind": kind,
                "valid_from": start.isoformat(),
                "valid_to": end.isoformat(),
                "value_inc_vat": 25,
                "payment_method": payment_method,
            }
        ]


@pytest.mark.asyncio
async def test_sync_fetches_account_readings_and_prices_before_persisting():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    store = MemoryStore()
    settings = Settings(
        _env_file=None, service_token="a" * 32, api_key="fixture-key", account_number="A-12345678"
    )
    manager = SyncManager(store, settings, FakeClient)
    await manager.synchronize(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    assert len(store.meter_list) == 1
    assert len(store.readings) == 1
    assert {r["kind"] for r in store.prices} == {"standing", "unit"}
    assert store.get_meta("sync")["last_success"] is not None
    assert manager.running is False


@pytest.mark.asyncio
async def test_sync_rejects_concurrent_runs_before_network():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncBusyError, SyncManager

    manager = SyncManager(
        MemoryStore(),
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FakeClient,
    )
    manager.running = True
    with pytest.raises(SyncBusyError):
        await manager.synchronize()


class FailedClient(FakeClient):
    async def account(self, account_number):
        raise RuntimeError("private secret must not be logged")


@pytest.mark.asyncio
async def test_failed_sync_preserves_last_success_and_redacts_error():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    store = MemoryStore()
    store.set_meta("sync", {"last_success": "2026-01-01T00:00:00+00:00"})
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FailedClient,
    )
    result = await manager.synchronize(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert result["success"] is False
    assert store.get_meta("sync")["last_success"] == "2026-01-01T00:00:00+00:00"
    assert "private secret" not in str(store.metadata)
    assert store.readings == []
    assert not manager.running


class HistoryClient(FakeClient):
    async def account(self, account_number):
        result = await super().account(account_number)
        result["properties"][0]["electricity_meter_points"][0]["agreements"].insert(
            0,
            {
                "tariff_code": "E-1R-VAR-22-11-01-A",
                "valid_from": "2022-01-01T00:00:00Z",
                "valid_to": "2024-01-01T00:00:00Z",
            },
        )
        return result


@pytest.mark.asyncio
async def test_backfill_is_chunked_and_rates_skip_expired_agreements():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    HistoryClient.calls.clear()
    HistoryClient.rate_calls.clear()
    store = MemoryStore()
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        HistoryClient,
    )
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 7, tzinfo=UTC)
    result = await manager.synchronize(start, end)
    assert result["success"] is True
    assert len(HistoryClient.calls) == 3
    assert all(b - a <= timedelta(days=30) for a, b in HistoryClient.calls)
    assert all(tariff == "E-1R-AGILE-24-10-01-A" for tariff, a, b in HistoryClient.rate_calls)
    assert max(b for tariff, a, b in HistoryClient.rate_calls) == end + timedelta(days=2)


@pytest.mark.asyncio
async def test_default_sync_catches_up_since_last_success_and_rechecks_corrections():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    FakeClient.calls.clear()
    last = datetime.now(UTC) - timedelta(days=20)
    store = MemoryStore()
    store.set_meta("sync", {"last_success": last.isoformat()})
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FakeClient,
    )
    assert (await manager.synchronize())["success"]
    assert FakeClient.calls[0][0] == last - timedelta(days=7)


@pytest.mark.asyncio
async def test_background_sync_reserves_busy_flag_and_shutdown_cancels_cleanly():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncBusyError, SyncManager

    entered = asyncio.Event()

    class BlockingClient(FakeClient):
        async def account(self, account_number):
            entered.set()
            await asyncio.Event().wait()

    manager = SyncManager(
        MemoryStore(),
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        BlockingClient,
    )
    manager.trigger()
    assert manager.running
    with pytest.raises(SyncBusyError):
        manager.trigger()
    await asyncio.wait_for(entered.wait(), timeout=2)
    await manager.close()
    assert not manager.running


@pytest.mark.asyncio
async def test_scheduler_runs_at_startup_and_offline_mode_disables_it():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    store = MemoryStore()
    done = asyncio.Event()

    class NotifyingClient(FakeClient):
        async def account(self, account_number):
            done.set()
            return await super().account(account_number)

    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        NotifyingClient,
    )
    manager.launch_scheduler()
    await asyncio.wait_for(done.wait(), timeout=2)
    await manager.close()
    offline = SyncManager(MemoryStore(), Settings(_env_file=None, service_token="a" * 32))
    assert offline.launch_scheduler() is None
    await offline.close()


@pytest.mark.asyncio
async def test_unsupported_tariff_still_syncs_readings_and_reports_warning():
    from octopus_service.client import UnsupportedTariffError
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    class DualRegisterClient(FakeClient):
        async def account(self, account_number):
            result = await super().account(account_number)
            result["properties"][0]["electricity_meter_points"][0]["agreements"][0][
                "tariff_code"
            ] = "E-2R-VAR-22-11-01-A"
            return result

        async def rates(self, *args, **kwargs):
            raise UnsupportedTariffError("unsupported dual-register price")

    store = MemoryStore()
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        DualRegisterClient,
    )
    result = await manager.synchronize(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert result["success"]
    assert len(store.readings) == 1
    assert store.get_meta("sync")["warnings"]


@pytest.mark.asyncio
async def test_sync_uses_atomic_store_write_and_preserves_data_on_database_failure():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    class BrokenStore(MemoryStore):
        def apply_sync(self, meters, consumption, rates, meta):
            raise OSError("disk full; private path is not exposed")

    store = BrokenStore()
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FakeClient,
    )
    result = await manager.synchronize(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert result["success"] is False
    assert store.meter_list == []
    assert store.readings == []
    assert "private path" not in str(store.metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 1, 1), datetime(2026, 1, 2, tzinfo=UTC)),
        (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2020, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
async def test_invalid_sync_ranges_are_rejected_without_leaving_busy(start, end):
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    manager = SyncManager(
        MemoryStore(),
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FakeClient,
    )
    with pytest.raises(ValueError):
        await manager.synchronize(start, end)
    assert not manager.running


@pytest.mark.asyncio
async def test_future_sync_cannot_advance_checkpoint_past_real_time():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    store = MemoryStore()
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FakeClient,
    )
    with pytest.raises(ValueError):
        await manager.synchronize(datetime.now(UTC), datetime.now(UTC) + timedelta(days=1))
    assert not manager.running
    assert store.get_meta("sync") is None


@pytest.mark.asyncio
async def test_historical_backfill_advances_data_checkpoint_not_wall_clock():
    from octopus_service.settings import Settings
    from octopus_service.sync import SyncManager

    store = MemoryStore()
    FakeClient.calls.clear()
    manager = SyncManager(
        store,
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
        ),
        FakeClient,
    )
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    assert (await manager.synchronize(start, end))["success"]
    assert store.get_meta("sync")["synced_until"] == end.isoformat()
    assert (await manager.synchronize(end - timedelta(days=10), end - timedelta(days=9)))["success"]
    assert store.get_meta("sync")["synced_until"] == end.isoformat()
