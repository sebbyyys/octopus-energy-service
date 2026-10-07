"""Synchronization coordinator."""

import asyncio
from datetime import UTC, datetime, timedelta

from octopus_service.settings import Settings


class SyncBusyError(RuntimeError):
    """A synchronization is already in progress."""


class SyncManager:
    def __init__(self, store, settings: Settings, client_factory=None):
        self.store = store
        self.settings = settings
        self.client_factory = client_factory
        self.running = False
        self._task = None
        self._scheduler_task = None

    def _reserve(self, start=None, end=None):
        if not self.settings.account_number:
            raise ValueError("Octopus credentials are not configured")
        if self.running:
            raise SyncBusyError("A synchronization is already in progress")
        end = end or datetime.now(UTC)
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("sync timestamps must be timezone-aware")
        if start is None:
            meta = self.store.get_meta("sync") or {}
            checkpoint = meta.get("synced_until") or meta.get("last_success")
            start = (
                (
                    min(datetime.fromisoformat(checkpoint), end)
                    - timedelta(days=self.settings.lookback_days)
                )
                if checkpoint
                else (end - timedelta(days=self.settings.initial_days))
            )
            start = max(start, end - timedelta(days=730))
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("sync timestamps must be timezone-aware")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end > datetime.now(UTC):
            raise ValueError("consumption synchronization cannot request a future end")
        if end <= start or end - start > timedelta(days=730):
            raise ValueError("sync range must be positive and no longer than 730 days")
        self.running = True
        return start, end

    def trigger(self, start=None, end=None):
        start, end = self._reserve(start, end)
        self._task = asyncio.create_task(self._perform(start, end))
        return self._task

    def launch_scheduler(self):
        if not self.settings.account_number:
            return None
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(self._schedule())
        return self._scheduler_task

    async def _schedule(self):
        while True:
            if not self.running:
                await self.synchronize()
            await asyncio.sleep(self.settings.sync_interval_seconds)

    async def close(self):
        tasks = [t for t in (self._task, self._scheduler_task) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.running = False

    async def synchronize(self, start=None, end=None):
        start, end = self._reserve(start, end)
        return await self._perform(start, end)

    async def _perform(self, start=None, end=None):
        from octopus_service.client import OctopusClient, UnsupportedTariffError, discover_meters

        readings, prices, warnings = [], [], []
        try:
            async with (self.client_factory or OctopusClient)(
                self.settings.api_key.get_secret_value()
            ) as client:
                meters = discover_meters(await client.account(self.settings.account_number))
                for meter in meters:
                    meter["gas_unit"] = self.settings.gas_units_json.get(
                        meter["id"], meter.get("gas_unit", "unknown")
                    )
                    cursor = start
                    while cursor < end:
                        chunk_end = min(cursor + timedelta(days=30), end)
                        readings.extend(await client.consumption(meter, cursor, chunk_end))
                        cursor = chunk_end
                    for agreement in meter["agreements"]:
                        rate_start = max(start, datetime.fromisoformat(agreement["valid_from"]))
                        rate_end = min(
                            end + timedelta(days=2),
                            datetime.fromisoformat(agreement["valid_to"])
                            if agreement["valid_to"]
                            else end + timedelta(days=2),
                        )
                        if rate_end <= rate_start:
                            continue
                        for kind in ("unit", "standing"):
                            try:
                                prices.extend(
                                    await client.rates(
                                        meter,
                                        agreement["tariff_code"],
                                        kind,
                                        rate_start,
                                        rate_end,
                                        self.settings.payment_method,
                                    )
                                )
                            except UnsupportedTariffError:
                                warnings.append(f"Unsupported tariff: {agreement['tariff_code']}")
            previous = self.store.get_meta("sync") or {}
            checkpoint = (
                max(end, datetime.fromisoformat(previous["synced_until"]))
                if (previous.get("synced_until"))
                else end
            )
            meta = {
                "last_success": datetime.now(UTC).isoformat(),
                "synced_until": checkpoint.isoformat(),
                "period_from": start.isoformat(),
                "period_to": end.isoformat(),
                "warnings": sorted(set(warnings)),
                "readings_fetched": len(readings),
                "rates_fetched": len(prices),
                "last_error": None,
            }
            if hasattr(self.store, "apply_sync"):
                self.store.apply_sync(meters, readings, prices, meta)
            else:
                self.store.save_meters(meters)
                self.store.upsert_consumption(readings)
                self.store.upsert_rates(prices)
                self.store.set_meta("sync", meta)
            return {"success": True}
        except Exception as exc:
            meta = self.store.get_meta("sync") or {}
            meta["last_error"] = f"Synchronization failed ({type(exc).__name__})"
            self.store.set_meta("sync", meta)
            return {"success": False}
        finally:
            self.running = False
