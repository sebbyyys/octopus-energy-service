from datetime import UTC, datetime

import httpx
import pytest

from octopus_service import client as client_module
from octopus_service.client import OctopusClient, OctopusError


@pytest.mark.parametrize(
    "tariff,expected",
    [
        ("E-1R-AGILE-FLEX-22-11-25-A", "AGILE-FLEX-22-11-25"),
        ("G-1R-VAR-22-11-01-P", "VAR-22-11-01"),
        ("E-2R-VAR-22-11-01-N", "VAR-22-11-01"),
    ],
)
def test_product_code_parses_only_full_tariff_codes(tariff, expected):
    assert client_module.product_code(tariff) == expected


@pytest.mark.parametrize(
    "tariff",
    [
        "",
        None,
        123,
        "E-1R--A",
        "E-3R-VAR-22-11-01-A",
        "G-2R-VAR-22-11-01-A",
        "E-1R-VAR-22-11-01-I",
        "E-1R-VAR-22-11-01-O",
        "E-1R-var-22-11-01-A",
        " E-1R-VAR-22-11-01-A",
        "E-1R-VAR-22-11-01-A\n",
        "E-1R-VAR--A",
        "E-1R-../accounts-A",
        "E-1R-VAR-22-11-01-A?x=1",
        "E-1R-VAR-22-11-01-AA",
    ],
)
def test_product_code_rejects_malformed_tariff_without_echoing_it(tariff):
    with pytest.raises(OctopusError, match="tariff") as caught:
        client_module.product_code(tariff)
    assert "../accounts" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"results": {}},
        {"results": [1], "next": None},
        {"results": [], "next": False},
        {"results": [], "next": ""},
        {"results": [], "next": None, "count": True},
        {"results": [], "next": None, "count": -1},
        {"results": [], "next": None, "count": "0"},
    ],
)
async def test_malformed_page_is_sanitized(payload):
    from octopus_service.client import OctopusError

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with OctopusClient(transport=transport) as client:
        with pytest.raises(OctopusError, match="response"):
            await client.products()


@pytest.mark.parametrize("mode", ["cycle", "limit"])
async def test_pagination_is_bounded(mode, monkeypatch):
    from octopus_service.client import OctopusError

    monkeypatch.setattr("octopus_service.client.MAX_PAGES", 3, raising=False)
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) <= 3, "page limit ignored"
        next_url = "https://api.octopus.energy/v1/products/"
        if mode == "limit":
            next_url += f"?page={len(calls) + 1}"
        return httpx.Response(200, json={"results": [], "next": next_url})

    async with OctopusClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError, match="pagination"):
            await client.products()
    assert len(calls) == (1 if mode == "cycle" else 3)


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/v1/products/?page=2",
        "http://api.octopus.energy/v1/products/?page=2",
        "https://api.octopus.energy:444/v1/products/?page=2",
        "https://user:pass@api.octopus.energy/v1/products/?page=2",
        "https://api.octopus.energy/v1/accounts/A-12341234/",
        "https://api.octopus.energy/v1/products/%2e%2e/accounts/",
        "https://api.octopus.energy/v1/products/?page=2#fragment",
    ],
)
async def test_unsafe_pagination_is_rejected_before_request(next_url):
    from octopus_service.client import OctopusError

    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) == 1, "unsafe link was followed"
        return httpx.Response(200, json={"results": [], "next": next_url})

    async with OctopusClient("test-secret", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError, match="pagination"):
            await client.products()
    assert len(calls) == 1


async def test_products_collect_all_pages():
    calls = []

    def handler(request):
        calls.append(request)
        page = request.url.params.get("page")
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": None if page else "https://api.octopus.energy/v1/products/?page=2",
                "results": [{"code": "SECOND" if page else "FIRST"}],
            },
        )

    async with OctopusClient(transport=httpx.MockTransport(handler)) as client:
        assert await client.products() == [{"code": "FIRST"}, {"code": "SECOND"}]
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 400, 302])
async def test_http_failures_are_sanitized_and_not_retried(status):
    from octopus_service.client import OctopusAuthError, OctopusError

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="test-secret private account body")

    async with OctopusClient("test-secret", transport=httpx.MockTransport(handler)) as client:
        error = OctopusAuthError if status in (401, 403) else OctopusError
        with pytest.raises(error) as caught:
            await client.account("A-12341234")
        assert "test-secret" not in str(caught.value)
        assert "private account body" not in str(caught.value)
    assert len(calls) == 1


async def test_products_are_public_and_context_closes_transport():
    class Transport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True

    def handler(request):
        assert request.url == "https://api.octopus.energy/v1/products/"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"count": 1, "next": None, "results": [{"code": "VAR"}]})

    transport = Transport(handler)
    async with OctopusClient("test-secret", transport=transport) as client:
        assert await client.products() == [{"code": "VAR"}]
    assert transport.closed


def account_fixture():
    return {
        "properties": [
            {"moved_out_at": "2025-01-01T00:00:00Z", "electricity_meter_points": "ignore"},
            {
                "moved_out_at": None,
                "electricity_meter_points": [
                    {
                        "mpan": "1000000000000",
                        "is_export": False,
                        "meters": [{"serial_number": "OLD"}, {"serial_number": "NEW"}],
                        "agreements": [
                            {
                                "tariff_code": "E-2R-VAR-22-11-01-A",
                                "valid_from": "2026-03-29T02:00:00+01:00",
                                "valid_to": None,
                            }
                        ],
                    },
                    {
                        "mpan": "2000000000000",
                        "is_export": True,
                        "meters": [{"serial_number": "EXPORT"}],
                        "agreements": [
                            {
                                "tariff_code": "E-1R-OUTGOING-19-05-13-A",
                                "valid_from": "2025-01-01T00:00:00Z",
                                "valid_to": "2026-12-31T00:00:00Z",
                            }
                        ],
                    },
                ],
                "gas_meter_points": [
                    {
                        "mprn": "1234567890",
                        "meters": [{"serial_number": "GAS"}],
                        "agreements": [
                            {
                                "tariff_code": "G-1R-VAR-22-11-01-A",
                                "valid_from": "2025-01-01T00:00:00Z",
                                "valid_to": None,
                            }
                        ],
                    },
                ],
            },
        ]
    }


def test_discovery_retains_all_serials_and_export_but_skips_moved_out_properties():
    account = account_fixture()
    meters = client_module.discover_meters(account)
    assert len(meters) == 4
    by_id = {meter["id"]: meter for meter in meters}
    assert by_id["electricity:1000000000000:NEW"] == {
        "id": "electricity:1000000000000:NEW",
        "fuel": "electricity",
        "meter_point": "1000000000000",
        "serial_number": "NEW",
        "is_export": False,
        "gas_unit": "unknown",
        "active": True,
        "agreements": [
            {
                "tariff_code": "E-2R-VAR-22-11-01-A",
                "valid_from": "2026-03-29T01:00:00+00:00",
                "valid_to": None,
            }
        ],
    }
    assert by_id["electricity:2000000000000:EXPORT"]["is_export"] is True
    assert by_id["gas:1234567890:GAS"]["gas_unit"] == "unknown"
    assert all(meter["active"] for meter in meters)
    # Discovery must not mutate the private account response.
    assert (
        account["properties"][1]["electricity_meter_points"][0]["agreements"][0]["valid_from"]
        == "2026-03-29T02:00:00+01:00"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("mpan", "../accounts"),
        ("mpan", 1000000000000),
        ("is_export", "false"),
        ("meters", None),
        ("agreements", {}),
        ("tariff_code", "G-1R-VAR-22-11-01-A"),
        ("tariff_code", "E-1R-../secret-A"),
        ("valid_from", "2026-01-01T00:00:00"),
        ("valid_from", "private-address"),
        ("valid_to", "2025-01-01T00:00:00Z"),
        ("serial_number", "../../secret"),
    ],
)
def test_discovery_rejects_invalid_active_meter_data(field, value):
    account = account_fixture()
    point = account["properties"][1]["electricity_meter_points"][0]
    if field in {"tariff_code", "valid_from", "valid_to"}:
        point["agreements"][0][field] = value
    elif field == "serial_number":
        point["meters"][0][field] = value
    else:
        point[field] = value
    with pytest.raises(OctopusError) as caught:
        client_module.discover_meters(account)
    assert "secret" not in str(caught.value)
    assert "private-address" not in str(caught.value)


@pytest.mark.parametrize("account", [None, [], {}, {"properties": None}, {"properties": [None]}])
def test_discovery_rejects_malformed_account(account):
    with pytest.raises(OctopusError):
        client_module.discover_meters(account)


async def test_account_uses_basic_key_with_empty_password():
    def handler(request):
        assert request.url.path == "/v1/accounts/A-12341234/"
        assert request.headers["authorization"] == httpx.BasicAuth("test-secret", "")._auth_header
        return httpx.Response(200, json={"number": "A-12341234", "properties": []})

    async with OctopusClient("test-secret", transport=httpx.MockTransport(handler)) as client:
        assert await client.account("A-12341234") == {"number": "A-12341234", "properties": []}


async def test_missing_key_fails_before_private_request():
    def handler(request):
        pytest.fail("private request sent without a key")

    async with OctopusClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(client_module.OctopusAuthError, match="credentials"):
            await client.account("A-12341234")


@pytest.mark.parametrize(
    "account_number",
    ["", None, "../products", "A-12341234?key=secret", "A-12341234/../products", "A-12341234\n"],
)
async def test_account_identifier_cannot_change_private_path(account_number):
    def handler(request):
        pytest.fail("invalid account identifier reached transport")

    async with OctopusClient("secret", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError, match="account"):
            await client.account(account_number)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="private malformed body"),
        httpx.Response(200, json=[]),
        httpx.Response(200, content=b"\xff"),
    ],
)
async def test_invalid_account_response_is_sanitized(response):
    async with OctopusClient("secret", transport=httpx.MockTransport(lambda r: response)) as client:
        with pytest.raises(OctopusError, match="response") as caught:
            await client.account("A-12341234")
        assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "counts,result_sizes",
    [([2], [1]), ([0], [1]), ([2, 3], [1, 1]), ([3, None], [1, 1]), ([None, 1], [1, 1])],
)
async def test_pagination_count_mismatch_rejects_incomplete_or_changed_response(
    counts, result_sizes
):
    calls = []

    def handler(request):
        index = len(calls)
        calls.append(request)
        payload = {
            "results": [{}] * result_sizes[index],
            "next": (
                f"https://api.octopus.energy/v1/products/?page={index + 2}"
                if index + 1 < len(counts)
                else None
            ),
        }
        if counts[index] is not None:
            payload["count"] = counts[index]
        return httpx.Response(200, json=payload)

    async with OctopusClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError, match="count"):
            await client.products()


async def test_relative_pagination_stays_on_same_endpoint():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "count": 2,
                "results": [{"page": len(calls)}],
                "next": "?page=2" if len(calls) == 1 else None,
            },
        )

    async with OctopusClient(transport=httpx.MockTransport(handler)) as client:
        assert await client.products() == [{"page": 1}, {"page": 2}]
    assert calls[1].url == "https://api.octopus.energy/v1/products/?page=2"


@pytest.mark.parametrize("failure", [429, 500, 503, 599, "timeout", "network"])
async def test_transient_failures_are_retried_with_bounded_delay(failure, monkeypatch):
    calls, sleeps = [], []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            if failure == "timeout":
                raise httpx.ReadTimeout("secret private account", request=request)
            if failure == "network":
                raise httpx.ConnectError("secret private account", request=request)
            return httpx.Response(failure, headers={"Retry-After": "999999999"}, text="secret")
        return httpx.Response(200, json={"results": [], "next": None})

    async with OctopusClient("secret", retries=2, transport=httpx.MockTransport(handler)) as client:
        assert await client.products() == []
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert all(0 <= delay <= 30 for delay in sleeps)
    assert all("authorization" not in request.headers for request in calls)


@pytest.mark.parametrize("failure", [429, 503, "network"])
async def test_exhausted_retries_raise_sanitized_error(failure, monkeypatch):
    calls = []

    async def sleep(delay):
        pass

    monkeypatch.setattr("asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        if failure == "network":
            raise httpx.ConnectError("secret account body", request=request)
        return httpx.Response(failure, text="secret account body")

    async with OctopusClient("secret", retries=1, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError) as caught:
            await client.account("A-12341234")
        assert "secret" not in str(caught.value)
        assert "account body" not in str(caught.value)
        assert caught.value.__cause__ is None
    assert len(calls) == 2


@pytest.mark.parametrize(
    "retry_after", ["2", "-20", "nan", "inf", "not-a-date", "Thu, 01 Jan 2099 00:00:00 GMT"]
)
async def test_retry_after_is_finite_and_bounded(retry_after, monkeypatch):
    calls, sleeps = [], []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": retry_after})
        return httpx.Response(200, json={"results": [], "next": None})

    async with OctopusClient(retries=1, transport=httpx.MockTransport(handler)) as client:
        assert await client.products() == []
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 30
    if retry_after == "2":
        assert sleeps == [2]


@pytest.mark.parametrize(
    "options",
    [
        {"retries": -1},
        {"retries": 1.5},
        {"retries": True},
        {"timeout": 0},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
    ],
)
def test_invalid_client_options_are_rejected(options):
    with pytest.raises(ValueError):
        OctopusClient(**options)


def meter_fixture(fuel="electricity"):
    return {
        "id": f"{fuel}:1234567890:SERIAL",
        "fuel": fuel,
        "meter_point": "1234567890",
        "serial_number": "SERIAL",
        "is_export": False,
        "gas_unit": "unknown",
        "active": True,
        "agreements": [
            {
                "tariff_code": f"{'E' if fuel == 'electricity' else 'G'}-1R-VAR-22-11-01-A",
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": None,
            }
        ],
    }


START = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
END = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


@pytest.mark.parametrize("fuel", ["electricity", "gas"])
async def test_consumption_private_pages_normalize_utc_without_converting_units(fuel):
    meter = meter_fixture(fuel)
    calls = []

    def handler(request):
        calls.append(request)
        path = f"/v1/{fuel}-meter-points/1234567890/meters/SERIAL/consumption/"
        assert request.url.path == path
        assert request.headers["authorization"] == httpx.BasicAuth("secret", "")._auth_header
        if len(calls) == 1:
            assert datetime.fromisoformat(request.url.params["period_from"]) == START
            assert datetime.fromisoformat(request.url.params["period_to"]) == END
            assert request.url.params["order_by"] == "period"
            assert int(request.url.params["page_size"]) > 0
        row = {
            "interval_start": "2026-03-29T02:00:00+01:00",
            "interval_end": "2026-03-29T02:30:00+01:00",
            "consumption": "0.025",
        }
        if len(calls) == 2:
            row = {
                "interval_start": "2026-03-29T00:30:00Z",
                "interval_end": "2026-03-29T02:00:00+01:00",
                "consumption": 1.234,
            }
        return httpx.Response(
            200, json={"count": 2, "results": [row], "next": "?page=2" if len(calls) == 1 else None}
        )

    async with OctopusClient("secret", transport=httpx.MockTransport(handler)) as client:
        assert await client.consumption(meter, START, END) == [
            {
                "meter_id": meter["id"],
                "interval_start": "2026-03-29T00:30:00+00:00",
                "interval_end": "2026-03-29T01:00:00+00:00",
                "consumption": 1.234,
            },
            {
                "meter_id": meter["id"],
                "interval_start": "2026-03-29T01:00:00+00:00",
                "interval_end": "2026-03-29T01:30:00+00:00",
                "consumption": 0.025,
            },
        ]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("consumption", -0.1),
        ("consumption", "NaN"),
        ("consumption", "Infinity"),
        ("consumption", True),
        ("consumption", None),
        ("consumption", "private-body"),
        ("consumption", "1e999"),
        ("interval_start", "2026-03-29T00:30:00"),
        ("interval_end", "2026-03-29T00:30:00Z"),
        ("interval_end", None),
    ],
)
async def test_invalid_consumption_is_rejected_without_returning_partial_data(field, value):
    row = {
        "interval_start": "2026-03-29T00:30:00Z",
        "interval_end": "2026-03-29T01:00:00Z",
        "consumption": 0,
    }
    row[field] = value
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "results": [row],
                "next": None,
            },
        )
    )
    async with OctopusClient("secret", transport=transport) as client:
        with pytest.raises(OctopusError) as caught:
            await client.consumption(meter_fixture(), START, END)
        assert "private-body" not in str(caught.value)


@pytest.mark.parametrize(
    "start,end",
    [
        (START.replace(tzinfo=None), END),
        (START, END.replace(tzinfo=None)),
        (START, START),
        (END, START),
        (None, END),
        (START.isoformat(), END),
    ],
)
async def test_invalid_consumption_range_fails_before_request(start, end):
    transport = httpx.MockTransport(lambda r: pytest.fail("invalid range reached transport"))
    async with OctopusClient("secret", transport=transport) as client:
        with pytest.raises(OctopusError):
            await client.consumption(meter_fixture(), start, end)


@pytest.mark.parametrize(
    "field,value",
    [
        ("fuel", "../accounts"),
        ("fuel", None),
        ("serial_number", "../../accounts"),
        ("meter_point", "123?secret"),
        ("id", "wrong"),
    ],
)
async def test_invalid_meter_cannot_change_consumption_path(field, value):
    meter = meter_fixture()
    meter[field] = value
    transport = httpx.MockTransport(lambda r: pytest.fail("unsafe meter reached transport"))
    async with OctopusClient("secret", transport=transport) as client:
        with pytest.raises(OctopusError):
            await client.consumption(meter, START, END)


async def test_consumption_uses_half_open_overlap_filter_without_clipping_readings():
    rows = [
        {
            "interval_start": f"2026-03-29T{start}:00Z",
            "interval_end": f"2026-03-29T{end}:00Z",
            "consumption": 1,
        }
        for start, end in [
            ("00:00", "00:30"),
            ("00:15", "00:45"),
            ("01:15", "01:45"),
            ("01:30", "02:00"),
        ]
    ]
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "results": rows,
                "count": 4,
                "next": None,
            },
        )
    )
    async with OctopusClient("secret", transport=transport) as client:
        result = await client.consumption(meter_fixture(), START, END)
    assert [(r["interval_start"], r["interval_end"]) for r in result] == [
        ("2026-03-29T00:15:00+00:00", "2026-03-29T00:45:00+00:00"),
        ("2026-03-29T01:15:00+00:00", "2026-03-29T01:45:00+00:00"),
    ]


@pytest.mark.parametrize(
    "fuel,kind,payment_method",
    [
        ("electricity", "unit", "DIRECT_DEBIT"),
        ("gas", "unit", "NON_DIRECT_DEBIT"),
        ("electricity", "standing", "NON_DIRECT_DEBIT"),
        ("gas", "standing", "DIRECT_DEBIT"),
    ],
)
async def test_public_rates_normalize_utc_and_select_requested_payment_method(
    fuel, kind, payment_method
):
    meter = meter_fixture(fuel)
    tariff = meter["agreements"][0]["tariff_code"]
    calls = []

    def handler(request):
        calls.append(request)
        endpoint = "standard-unit-rates" if kind == "unit" else "standing-charges"
        assert request.url.path == f"/v1/products/VAR-22-11-01/{fuel}-tariffs/{tariff}/{endpoint}/"
        assert "authorization" not in request.headers
        assert datetime.fromisoformat(request.url.params["period_from"]) == START
        assert datetime.fromisoformat(request.url.params["period_to"]) == END
        assert "order_by" not in request.url.params
        rows = [
            {
                "valid_from": "2026-03-29T02:00:00+01:00",
                "valid_to": None,
                "value_inc_vat": "-2.15" if kind == "unit" else "50.15",
                "payment_method": method,
            }
            for method in ("DIRECT_DEBIT", "NON_DIRECT_DEBIT")
        ]
        rows.append(
            {
                "valid_from": "2026-03-29T00:00:00Z",
                "valid_to": "2026-03-29T02:00:00+01:00",
                "value_inc_vat": 10,
                "payment_method": None,
            }
        )
        return httpx.Response(200, json={"count": 3, "results": rows, "next": None})

    async with OctopusClient(transport=httpx.MockTransport(handler)) as client:
        assert await client.rates(meter, tariff, kind, START, END, payment_method) == [
            {
                "meter_id": meter["id"],
                "tariff_code": tariff,
                "kind": kind,
                "valid_from": "2026-03-29T00:00:00+00:00",
                "valid_to": "2026-03-29T01:00:00+00:00",
                "value_inc_vat": 10,
                "payment_method": None,
            },
            {
                "meter_id": meter["id"],
                "tariff_code": tariff,
                "kind": kind,
                "valid_from": "2026-03-29T01:00:00+00:00",
                "valid_to": None,
                "value_inc_vat": -2.15 if kind == "unit" else 50.15,
                "payment_method": payment_method,
            },
        ]
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["unit", "standing"])
async def test_economy7_rates_raise_unsupported_without_request(kind):
    meter = meter_fixture()
    tariff = "E-2R-VAR-22-11-01-A"
    meter["agreements"][0]["tariff_code"] = tariff
    transport = httpx.MockTransport(lambda r: pytest.fail("Economy7 queried as single-register"))
    async with OctopusClient(transport=transport) as client:
        with pytest.raises(OctopusError) as caught:
            await client.rates(meter, tariff, kind, START, END)
        assert type(caught.value).__name__ == "UnsupportedTariffError"


@pytest.mark.parametrize(
    "override",
    [
        {"tariff_code": "G-1R-VAR-22-11-01-A"},
        {"tariff_code": "E-1R-../private-A"},
        {"kind": "day"},
        {"kind": None},
        {"payment_method": None},
        {"payment_method": "PREPAYMENT"},
        {"start": START.replace(tzinfo=None)},
        {"end": START},
    ],
)
async def test_invalid_rate_request_is_rejected_before_request(override):
    arguments = {
        "meter": meter_fixture(),
        "tariff_code": "E-1R-VAR-22-11-01-A",
        "kind": "unit",
        "start": START,
        "end": END,
    }
    arguments.update(override)
    transport = httpx.MockTransport(lambda r: pytest.fail("invalid rate request sent"))
    async with OctopusClient(transport=transport) as client:
        with pytest.raises(OctopusError):
            await client.rates(**arguments)


@pytest.mark.parametrize(
    "field,value,kind",
    [
        ("value_inc_vat", "NaN", "unit"),
        ("value_inc_vat", True, "unit"),
        ("value_inc_vat", "1e999", "unit"),
        ("value_inc_vat", None, "unit"),
        ("value_inc_vat", -1, "standing"),
        ("valid_from", "private-body", "unit"),
        ("valid_to", "2026-03-29T00:00:00Z", "unit"),
        ("valid_from", "2026-03-29T00:00:00", "unit"),
        ("payment_method", "PREPAYMENT", "unit"),
        ("payment_method", False, "unit"),
    ],
)
async def test_invalid_rate_rows_are_sanitized(field, value, kind):
    row = {
        "valid_from": "2026-03-29T00:00:00Z",
        "valid_to": None,
        "value_inc_vat": 10,
        "payment_method": None,
    }
    row[field] = value
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "results": [row],
                "next": None,
            },
        )
    )
    async with OctopusClient(transport=transport) as client:
        with pytest.raises(OctopusError) as caught:
            await client.rates(meter_fixture(), "E-1R-VAR-22-11-01-A", kind, START, END)
        assert "private-body" not in str(caught.value)


async def test_rates_intersect_each_matching_agreement_without_filling_gaps():
    meter = meter_fixture()
    tariff = "E-1R-VAR-22-11-01-A"
    meter["agreements"] = [
        {
            "tariff_code": tariff,
            "valid_from": "2026-03-29T00:20:00Z",
            "valid_to": "2026-03-29T00:50:00Z",
        },
        {
            "tariff_code": "E-1R-GO-22-10-14-A",
            "valid_from": "2026-03-29T00:50:00Z",
            "valid_to": "2026-03-29T01:10:00Z",
        },
        {"tariff_code": tariff, "valid_from": "2026-03-29T02:10:00+01:00", "valid_to": None},
    ]
    rows = [
        {
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": None,
            "value_inc_vat": 10,
            "payment_method": None,
        },
        {
            "valid_from": "2026-03-28T00:00:00Z",
            "valid_to": "2026-03-29T00:30:00Z",
            "value_inc_vat": 9,
            "payment_method": None,
        },
        {
            "valid_from": "2026-03-29T01:30:00Z",
            "valid_to": None,
            "value_inc_vat": 11,
            "payment_method": None,
        },
    ]
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "count": 3,
                "results": rows,
                "next": None,
            },
        )
    )
    async with OctopusClient("secret", transport=transport) as client:
        result = await client.rates(meter, tariff, "unit", START, END)
    assert [(r["valid_from"], r["valid_to"], r["value_inc_vat"]) for r in result] == [
        ("2026-03-29T00:20:00+00:00", "2026-03-29T00:50:00+00:00", 10),
        ("2026-03-29T01:10:00+00:00", None, 10),
    ]


@pytest.mark.parametrize(
    "agreements",
    [
        [],
        [
            {
                "tariff_code": "E-1R-VAR-22-11-01-A",
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": "2026-03-29T00:30:00Z",
            }
        ],
        [
            {
                "tariff_code": "E-1R-VAR-22-11-01-A",
                "valid_from": "2026-03-29T01:30:00Z",
                "valid_to": None,
            }
        ],
        [
            {
                "tariff_code": "E-1R-GO-22-10-14-A",
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": None,
            }
        ],
    ],
)
async def test_no_matching_agreement_in_range_returns_no_rates_without_request(agreements):
    meter = meter_fixture()
    meter["agreements"] = agreements
    transport = httpx.MockTransport(lambda r: pytest.fail("unagreed tariff requested"))
    async with OctopusClient(transport=transport) as client:
        assert await client.rates(meter, "E-1R-VAR-22-11-01-A", "unit", START, END) == []


async def test_rates_validate_agreements_before_request():
    meter = meter_fixture()
    meter["agreements"][0]["valid_to"] = "2025-01-01T00:00:00Z"
    transport = httpx.MockTransport(lambda r: pytest.fail("invalid agreement reached transport"))
    async with OctopusClient(transport=transport) as client:
        with pytest.raises(OctopusError, match="agreement"):
            await client.rates(meter, "E-1R-VAR-22-11-01-A", "unit", START, END)


@pytest.mark.parametrize("field", ["meters", "agreements"])
def test_discovery_requires_complete_meter_point_fields(field):
    account = account_fixture()
    del account["properties"][1]["electricity_meter_points"][0][field]
    with pytest.raises(OctopusError, match="response"):
        client_module.discover_meters(account)


async def test_rate_requires_explicit_valid_to_in_response():
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "valid_from": "2026-01-01T00:00:00Z",
                        "value_inc_vat": 10,
                        "payment_method": None,
                    }
                ],
                "next": None,
            },
        )
    )
    async with OctopusClient(transport=transport) as client:
        with pytest.raises(OctopusError, match="response"):
            await client.rates(meter_fixture(), "E-1R-VAR-22-11-01-A", "unit", START, END)


@pytest.mark.parametrize(
    "next_url",
    [
        "//evil.example/steal",
        "https://api.octopus.energy/v1/accounts/A-12341234/",
        "https://[malformed/",
        "https://api.octopus.energy/v1/products/?secret\n=1",
        "https://api.octopus.energy:444/v1/electricity-meter-points/1234567890/meters/SERIAL/consumption/",
    ],
)
async def test_private_pagination_never_sends_key_to_unsafe_link(next_url):
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) == 1, "unsafe private pagination was followed"
        assert request.headers["authorization"] == httpx.BasicAuth("secret", "")._auth_header
        return httpx.Response(200, json={"results": [], "next": next_url})

    async with OctopusClient("secret", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError, match="pagination") as caught:
            await client.consumption(meter_fixture(), START, END)
        assert "secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_private_redirects_are_not_followed(status):
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) == 1, "private redirect was followed"
        return httpx.Response(status, headers={"Location": "https://evil.example/steal"})

    async with OctopusClient("secret", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OctopusError, match=f"HTTP {status}"):
            await client.account("A-12341234")
    assert len(calls) == 1


async def test_rate_pagination_with_configured_key_remains_public():
    calls = []

    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        row = {
            "valid_from": "2026-03-29T00:30:00Z" if len(calls) == 1 else "2026-03-29T01:00:00Z",
            "valid_to": "2026-03-29T01:30:00Z",
            "value_inc_vat": -1,
            "payment_method": None,
        }
        return httpx.Response(
            200, json={"count": 2, "results": [row], "next": "?page=2" if len(calls) == 1 else None}
        )

    async with OctopusClient("secret", transport=httpx.MockTransport(handler)) as client:
        result = await client.rates(meter_fixture(), "E-1R-VAR-22-11-01-A", "unit", START, END)
    assert len(result) == 2
    assert len(calls) == 2
