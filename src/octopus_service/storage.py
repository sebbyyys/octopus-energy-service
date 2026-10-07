"""Thread-safe SQLite persistence; consumption stays in its original meter unit."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock


def _utc(value: str | datetime) -> str:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _identifiers(record, keys):
    if any(not isinstance(record.get(key), str) or not record[key] for key in keys):
        raise ValueError("identifiers must be nonempty strings")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._transaction_depth = 0
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS meters (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS consumption (meter_id TEXT, interval_start TEXT, interval_end TEXT NOT NULL, consumption REAL NOT NULL, PRIMARY KEY(meter_id, interval_start))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS rates (meter_id TEXT, tariff_code TEXT, kind TEXT, valid_from TEXT, valid_to TEXT, value_inc_vat REAL NOT NULL, payment_method TEXT NOT NULL, PRIMARY KEY(meter_id, tariff_code, kind, valid_from, payment_method))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._db.commit()

    @contextmanager
    def _transaction(self):
        """Nested writes share the outer transaction and its lock."""
        with self._lock:
            outermost = self._transaction_depth == 0
            self._transaction_depth += 1
            try:
                if outermost:
                    with self._db:
                        yield
                else:
                    yield
            finally:
                self._transaction_depth -= 1

    def apply_sync(
        self, meters: list[dict], consumption: list[dict], rates: list[dict], meta: dict
    ) -> dict:
        """Atomically persist a complete successful sync; meta is the sync dictionary."""
        with self._transaction():
            self.save_meters(meters)
            inserted_consumption = self.upsert_consumption(consumption)
            inserted_rates = self.upsert_rates(rates)
            self.set_meta("sync", meta)
        return {"consumption": inserted_consumption, "rates": inserted_rates}

    def set_meta(self, key: str, value: dict):
        if not isinstance(value, dict):
            raise ValueError("metadata must be a dictionary")
        data = json.dumps(value, allow_nan=False)
        with self._transaction():
            self._db.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                (key, data),
            )

    def get_meta(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def upsert_rates(self, records: list[dict]) -> int:
        inserted = 0
        with self._transaction():
            for record in records:
                _identifiers(record, ("meter_id", "tariff_code"))
                start = _utc(record["valid_from"])
                end = _utc(record["valid_to"]) if record["valid_to"] is not None else None
                value = record["value_inc_vat"]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError("rate must be finite")
                if end is not None and end <= start:
                    raise ValueError("rate duration must be positive")
                if record["kind"] not in ("unit", "standing"):
                    raise ValueError("invalid rate kind")
                payment = record.get("payment_method")
                if payment not in (None, "DIRECT_DEBIT", "NON_DIRECT_DEBIT"):
                    raise ValueError("invalid payment method")
                identity = (
                    record["meter_id"],
                    record["tariff_code"],
                    record["kind"],
                    start,
                    payment or "",
                )
                inserted += self._db.execute(
                    "INSERT OR IGNORE INTO rates VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*identity[:4], end, value, identity[4]),
                ).rowcount
                self._db.execute(
                    "UPDATE rates SET valid_to=?, value_inc_vat=? WHERE meter_id=? AND tariff_code=? AND kind=? AND valid_from=? AND payment_method=?",
                    (end, value, *identity),
                )
        return inserted

    def rates(self, start: datetime, end: datetime, meter_id: str | None = None) -> list[dict]:
        query = "SELECT meter_id, tariff_code, kind, valid_from, valid_to, value_inc_vat, payment_method FROM rates WHERE valid_from < ? AND (valid_to IS NULL OR valid_to > ?)"
        args = [_utc(end), _utc(start)]
        if meter_id is not None:
            query += " AND meter_id = ?"
            args.append(meter_id)
        with self._lock:
            result = [
                dict(
                    zip(
                        (
                            "meter_id",
                            "tariff_code",
                            "kind",
                            "valid_from",
                            "valid_to",
                            "value_inc_vat",
                            "payment_method",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in self._db.execute(
                    query + " ORDER BY valid_from, meter_id, tariff_code, kind, payment_method",
                    args,
                )
            ]
        for row in result:
            row["payment_method"] = row["payment_method"] or None
        return result

    def upsert_consumption(self, records: list[dict]) -> int:
        inserted = 0
        with self._transaction():
            for record in records:
                _identifiers(record, ("meter_id",))
                start = _utc(record["interval_start"])
                end = _utc(record["interval_end"])
                value = record["consumption"]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("consumption must be finite and nonnegative")
                if end <= start:
                    raise ValueError("interval duration must be positive")
                inserted += self._db.execute(
                    "INSERT OR IGNORE INTO consumption VALUES (?, ?, ?, ?)",
                    (record["meter_id"], start, end, record["consumption"]),
                ).rowcount
                self._db.execute(
                    "UPDATE consumption SET interval_end=?, consumption=? WHERE meter_id=? AND interval_start=?",
                    (end, record["consumption"], record["meter_id"], start),
                )
        return inserted

    def consumption(
        self, start: datetime, end: datetime, meter_id: str | None = None
    ) -> list[dict]:
        query = "SELECT meter_id, interval_start, interval_end, consumption FROM consumption WHERE interval_start < ? AND interval_end > ?"
        args = [_utc(end), _utc(start)]
        if meter_id is not None:
            query += " AND meter_id = ?"
            args.append(meter_id)
        with self._lock:
            return [
                dict(
                    zip(
                        ("meter_id", "interval_start", "interval_end", "consumption"),
                        row,
                        strict=True,
                    )
                )
                for row in self._db.execute(query + " ORDER BY interval_start, meter_id", args)
            ]

    def consumption_bounds(self, meter_id: str | None = None) -> dict:
        """UTC bounds and record count for cumulative-history queries; empty bounds are null."""
        query = "SELECT MIN(interval_start), MAX(interval_end), COUNT(*) FROM consumption"
        args = ()
        if meter_id is not None:
            query += " WHERE meter_id=?"
            args = (meter_id,)
        with self._lock:
            row = self._db.execute(query, args).fetchone()
        return dict(zip(("start", "end", "count"), row, strict=True))

    def close(self):
        with self._lock:
            self._db.close()

    def save_meters(self, meters: list[dict]):
        with self._transaction():
            for old in self.meters():
                old["active"] = False
                self._db.execute(
                    "UPDATE meters SET data=? WHERE id=?", (json.dumps(old), old["id"])
                )
            for original in meters:
                meter = dict(original)
                _identifiers(meter, ("id", "meter_point", "serial_number"))
                if meter.get("fuel") not in ("electricity", "gas"):
                    raise ValueError("invalid fuel")
                if meter.get("gas_unit") not in ("unknown", "kwh", "m3"):
                    raise ValueError("invalid gas unit")
                if any(not isinstance(meter.get(key), bool) for key in ("active", "is_export")):
                    raise ValueError("meter flags must be booleans")
                agreements = []
                for original_agreement in meter.get("agreements", []):
                    agreement = dict(original_agreement)
                    agreement["valid_from"] = _utc(agreement["valid_from"])
                    agreement["valid_to"] = (
                        _utc(agreement["valid_to"])
                        if agreement.get("valid_to") is not None
                        else None
                    )
                    if (
                        agreement["valid_to"] is not None
                        and agreement["valid_to"] <= agreement["valid_from"]
                    ):
                        raise ValueError("agreement duration must be positive")
                    agreements.append(agreement)
                meter["agreements"] = agreements
                self._db.execute(
                    "INSERT INTO meters VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (meter["id"], json.dumps(meter, allow_nan=False)),
                )

    def meters(self) -> list[dict]:
        with self._lock:
            return [
                json.loads(row[0])
                for row in self._db.execute("SELECT data FROM meters ORDER BY id")
            ]
