"""Async Octopus Energy UK REST client."""

import asyncio
import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import httpx

MAX_PAGES = 1000
_TARIFF = re.compile(r"(?:E-[12]R|G-1R)-(?P<product>[A-Z0-9]+(?:-[A-Z0-9]+)*)-[A-HJ-NP]")


class OctopusError(Exception):
    """Safe error message suitable for service logs."""


class OctopusAuthError(OctopusError):
    """Missing or rejected private endpoint credentials."""


class UnsupportedTariffError(OctopusError):
    """Tariff needs register-specific pricing not supported by this client."""


def product_code(tariff_code: str) -> str:
    """Extract a product without accepting malformed or URL-like tariff tokens."""
    match = _TARIFF.fullmatch(tariff_code) if isinstance(tariff_code, str) else None
    if match is None:
        raise OctopusError("Invalid Octopus tariff code")
    return match.group("product")


def _datetime(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise OctopusError("Invalid Octopus timestamp") from None


def _objects(value) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise OctopusError("Malformed Octopus response")
    return value


def _identifier(value, *, numeric=False) -> str:
    pattern = r"[0-9]+" if numeric else r"[A-Za-z0-9_-]+"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise OctopusError("Invalid Octopus meter identifier")
    return value


def _agreement(agreement: dict, fuel: str) -> dict:
    code = agreement.get("tariff_code")
    product_code(code)
    if code[0] != ("E" if fuel == "electricity" else "G"):
        raise OctopusError("Octopus tariff fuel mismatch")
    start = _datetime(agreement.get("valid_from"))
    end = _datetime(agreement["valid_to"]) if agreement.get("valid_to") is not None else None
    if end is not None and end <= start:
        raise OctopusError("Invalid Octopus agreement period")
    return {
        "tariff_code": code,
        "valid_from": start.isoformat(),
        "valid_to": end.isoformat() if end is not None else None,
    }


def discover_meters(account: dict) -> list[dict]:
    """Keep historic serials at occupied properties; never infer gas units."""
    if not isinstance(account, dict):
        raise OctopusError("Malformed Octopus account response")
    meters = []
    for prop in _objects(account.get("properties")):
        if prop.get("moved_out_at") is not None:
            continue
        for fuel, point_key in (("electricity", "mpan"), ("gas", "mprn")):
            for point in _objects(prop.get(f"{fuel}_meter_points", [])):
                meter_point = _identifier(point.get(point_key), numeric=True)
                is_export = point.get("is_export", False)
                if type(is_export) is not bool or (fuel == "gas" and is_export):
                    raise OctopusError("Malformed Octopus export flag")
                agreements = [_agreement(a, fuel) for a in _objects(point.get("agreements"))]
                for meter in _objects(point.get("meters")):
                    serial = _identifier(meter.get("serial_number"))
                    meters.append(
                        {
                            "id": f"{fuel}:{meter_point}:{serial}",
                            "fuel": fuel,
                            "meter_point": meter_point,
                            "serial_number": serial,
                            "is_export": is_export,
                            "gas_unit": "unknown",
                            "agreements": [dict(a) for a in agreements],
                            "active": True,
                        }
                    )
    return sorted(meters, key=lambda meter: meter["id"])


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    delay = 0.5 * 2 ** min(attempt, 6)
    if retry_after is not None:
        try:
            parsed = float(retry_after)
        except ValueError:
            try:
                parsed = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                parsed = delay
        if math.isfinite(parsed):
            delay = parsed
    return max(0.0, min(delay, 30.0))


def _range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise OctopusError("Invalid Octopus request period")
    start, end = _datetime(start), _datetime(end)
    if end <= start:
        raise OctopusError("Invalid Octopus request period")
    return start, end


def _meter(meter: dict) -> str:
    if not isinstance(meter, dict) or meter.get("fuel") not in ("electricity", "gas"):
        raise OctopusError("Invalid Octopus meter fuel")
    point = _identifier(meter.get("meter_point"), numeric=True)
    serial = _identifier(meter.get("serial_number"))
    if meter.get("id") != f"{meter['fuel']}:{point}:{serial}":
        raise OctopusError("Invalid Octopus meter identity")
    return meter["fuel"]


def _number(value, *, nonnegative=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise OctopusError("Invalid Octopus numeric value")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or (nonnegative and number < 0):
            raise ValueError
        result = float(number)
        if not math.isfinite(result):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise OctopusError("Invalid Octopus numeric value") from None


class OctopusClient:
    def __init__(self, api_key="", *, transport=None, timeout=20, retries=3):
        if type(retries) is not int or retries < 0:
            raise ValueError("retries must be a nonnegative integer")
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be positive and finite")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
        )
        self._retries = retries

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def _get(self, url, *, private=False):
        if private and not self._api_key.strip():
            raise OctopusAuthError("Octopus credentials are required")
        for attempt in range(self._retries + 1):
            try:
                response = await self._http.get(
                    url, auth=httpx.BasicAuth(self._api_key, "") if private else None
                )
            except httpx.RequestError:
                if attempt == self._retries:
                    raise OctopusError("Octopus network request failed") from None
                await asyncio.sleep(_retry_delay(None, attempt))
                continue
            if response.status_code in (401, 403):
                raise OctopusAuthError("Octopus authentication rejected")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt < self._retries:
                    await asyncio.sleep(_retry_delay(response.headers.get("Retry-After"), attempt))
                    continue
            if response.status_code != 200:
                raise OctopusError(f"Octopus request failed (HTTP {response.status_code})")
            try:
                return response.json()
            except (ValueError, UnicodeError):
                raise OctopusError("Malformed Octopus response") from None

    async def account(self, account_number):
        if (
            not isinstance(account_number, str)
            or re.fullmatch(r"A-[A-Z0-9]{8}", account_number) is None
        ):
            raise OctopusError("Invalid Octopus account number")
        payload = await self._get(
            f"https://api.octopus.energy/v1/accounts/{account_number}/", private=True
        )
        if not isinstance(payload, dict):
            raise OctopusError("Malformed Octopus account response")
        return payload

    async def _pages(self, url, *, private=False):
        records = []
        seen = set()
        base = httpx.URL(url)
        expected_count = None
        allowed_path = base.raw_path.split(b"?")[0]
        while url:
            try:
                candidate = base.join(url)
            except (TypeError, httpx.InvalidURL):
                raise OctopusError("Unsafe pagination URL") from None
            if (
                candidate.scheme != "https"
                or candidate.host != "api.octopus.energy"
                or candidate.port not in (None, 443)
                or candidate.userinfo
                or candidate.fragment
                or candidate.raw_path.split(b"?")[0] != allowed_path
            ):
                raise OctopusError("Unsafe pagination URL")
            if str(candidate) in seen or len(seen) >= MAX_PAGES:
                raise OctopusError("Octopus pagination cycle or page limit exceeded")
            seen.add(str(candidate))
            payload = await self._get(candidate, private=private)
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("results"), list)
                or not all(isinstance(item, dict) for item in payload["results"])
                or "next" not in payload
                or (
                    payload["next"] is not None
                    and (not isinstance(payload["next"], str) or not payload["next"])
                )
                or (
                    "count" in payload
                    and (type(payload["count"]) is not int or payload["count"] < 0)
                )
            ):
                raise OctopusError("Malformed Octopus response")
            if "count" in payload:
                if expected_count is not None and payload["count"] != expected_count:
                    raise OctopusError("Octopus pagination count changed")
                expected_count = payload["count"]
            records.extend(payload["results"])
            if expected_count is not None and len(records) > expected_count:
                raise OctopusError("Octopus pagination count mismatch")
            base = candidate
            url = payload["next"]
        if expected_count is not None and len(records) != expected_count:
            raise OctopusError("Octopus pagination count mismatch")
        return records

    async def products(self):
        return await self._pages("https://api.octopus.energy/v1/products/")

    async def consumption(self, meter: dict, start: datetime, end: datetime) -> list[dict]:
        fuel = _meter(meter)
        start, end = _range(start, end)
        url = httpx.URL(
            f"https://api.octopus.energy/v1/{fuel}-meter-points/"
            f"{meter['meter_point']}/meters/{meter['serial_number']}/consumption/",
            params={
                "period_from": start.isoformat(),
                "period_to": end.isoformat(),
                "page_size": 1000,
                "order_by": "period",
            },
        )
        records = await self._pages(url, private=True)
        normalized = []
        for row in records:
            interval_start = _datetime(row.get("interval_start"))
            interval_end = _datetime(row.get("interval_end"))
            value = _number(row.get("consumption"), nonnegative=True)
            if interval_end <= interval_start:
                raise OctopusError("Invalid Octopus consumption interval")
            if interval_start < end and interval_end > start:
                normalized.append(
                    {
                        "meter_id": meter["id"],
                        "interval_start": interval_start.isoformat(),
                        "interval_end": interval_end.isoformat(),
                        "consumption": value,
                    }
                )
        return sorted(normalized, key=lambda row: (row["interval_start"], row["interval_end"]))

    async def rates(
        self,
        meter: dict,
        tariff_code: str,
        kind: str,
        start: datetime,
        end: datetime,
        payment_method: str = "DIRECT_DEBIT",
    ) -> list[dict]:
        fuel = _meter(meter)
        start, end = _range(start, end)
        product = product_code(tariff_code)
        if tariff_code[0] != ("E" if fuel == "electricity" else "G"):
            raise OctopusError("Octopus tariff fuel mismatch")
        if tariff_code.startswith("E-2R-"):
            raise UnsupportedTariffError("Economy7 tariffs require separate day and night rates")
        if kind not in ("unit", "standing"):
            raise OctopusError("Invalid Octopus rate kind")
        if payment_method not in ("DIRECT_DEBIT", "NON_DIRECT_DEBIT"):
            raise OctopusError("Invalid Octopus payment method")
        windows = []
        for agreement in _objects(meter.get("agreements", [])):
            agreement = _agreement(agreement, fuel)
            agreement_start = _datetime(agreement["valid_from"])
            agreement_end = (
                _datetime(agreement["valid_to"]) if agreement["valid_to"] is not None else None
            )
            if (
                agreement["tariff_code"] == tariff_code
                and agreement_start < end
                and (agreement_end is None or agreement_end > start)
            ):
                windows.append((agreement_start, agreement_end))
        if not windows:
            return []
        endpoint = "standard-unit-rates" if kind == "unit" else "standing-charges"
        url = httpx.URL(
            f"https://api.octopus.energy/v1/products/{product}/{fuel}-tariffs/"
            f"{tariff_code}/{endpoint}/",
            params={
                "period_from": start.isoformat(),
                "period_to": end.isoformat(),
                "page_size": 1000,
            },
        )
        records = await self._pages(url)
        normalized = []
        for row in records:
            if "valid_to" not in row:
                raise OctopusError("Malformed Octopus rate response")
            method = row.get("payment_method")
            if method not in (None, "DIRECT_DEBIT", "NON_DIRECT_DEBIT"):
                raise OctopusError("Malformed Octopus payment method")
            valid_from = _datetime(row.get("valid_from"))
            valid_to = _datetime(row["valid_to"]) if row.get("valid_to") is not None else None
            value = _number(row.get("value_inc_vat"), nonnegative=kind == "standing")
            if valid_to is not None and valid_to <= valid_from:
                raise OctopusError("Invalid Octopus rate period")
            if method not in (None, payment_method):
                continue
            for agreement_start, agreement_end in windows:
                effective_start = max(valid_from, agreement_start)
                ends = [boundary for boundary in (valid_to, agreement_end) if boundary is not None]
                effective_end = min(ends) if ends else None
                if effective_start >= end or (
                    effective_end is not None
                    and (effective_end <= start or effective_end <= effective_start)
                ):
                    continue
                normalized.append(
                    {
                        "meter_id": meter["id"],
                        "tariff_code": tariff_code,
                        "kind": kind,
                        "valid_from": effective_start.isoformat(),
                        "valid_to": (
                            effective_end.isoformat() if effective_end is not None else None
                        ),
                        "value_inc_vat": value,
                        "payment_method": method,
                    }
                )
        return sorted(normalized, key=lambda row: row["valid_from"])
