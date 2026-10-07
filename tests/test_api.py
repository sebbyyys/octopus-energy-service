import asyncio
import threading

from fastapi.testclient import TestClient

from octopus_service.settings import Settings


def test_health_live_public_and_private_endpoints_require_token(tmp_path):
    from octopus_service.api import create_app

    app = create_app(
        Settings(_env_file=None, service_token="a" * 32, db_path=str(tmp_path / "test.db"))
    )
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/v1/status").status_code == 401
        response = client.get("/v1/status", headers={"Authorization": "Bearer " + "a" * 32})
        assert response.status_code == 200
        assert response.json()["configured"] is False
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_empty_offline_api_is_explicitly_unavailable(tmp_path):
    from octopus_service.api import create_app

    app = create_app(
        Settings(_env_file=None, service_token="a" * 32, db_path=str(tmp_path / "test.db"))
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "a" * 32
        assert client.get("/v1/meters").json() == {"meters": []}
        snapshot = client.get("/v1/home-assistant").json()
        assert snapshot["available"] is False
        assert snapshot["electricity"]["total_kwh"] is None
        assert snapshot["cost"]["month_gbp"] is None
        assert client.post("/v1/sync").status_code == 503
        assert client.get("/v1/openapi.json").status_code == 200


def test_configured_service_schedules_sync_and_rejects_duplicate_job(tmp_path):
    from octopus_service.api import create_app

    entered = threading.Event()

    class BlockingClient:
        def __init__(self, api_key):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def account(self, number):
            entered.set()
            await asyncio.Event().wait()

    app = create_app(
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
            db_path=str(tmp_path / "test.db"),
        ),
        BlockingClient,
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "a" * 32
        assert entered.wait(2), "Initial background synchronization did not start"
        assert client.get("/v1/status").json()["sync_running"] is True
        assert client.post("/v1/sync").status_code == 409
    assert app.state.sync.running is False


def test_manual_sync_accepts_explicit_backfill_and_rejects_invalid_ranges(tmp_path):
    from octopus_service.api import create_app

    class EmptyAccountClient:
        def __init__(self, api_key):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def account(self, account_number):
            return {"properties": []}

    app = create_app(
        Settings(
            _env_file=None,
            service_token="a" * 32,
            api_key="fixture-key",
            account_number="A-12345678",
            db_path=str(tmp_path / "test.db"),
        ),
        EmptyAccountClient,
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "a" * 32
        assert client.get("/v1/status").json()["sync_running"] is False
        assert (
            client.post(
                "/v1/sync", params={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00Z"}
            ).status_code
            == 422
        )
        accepted = client.post(
            "/v1/sync", params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"}
        )
        assert accepted.status_code == 202
        status = client.get("/v1/status").json()
        assert status["sync"]["period_from"] == "2026-01-01T00:00:00+00:00"


def test_analytics_endpoint_calculates_complete_interval_and_filters_meters(tmp_path):
    from octopus_service.api import create_app

    app = create_app(
        Settings(_env_file=None, service_token="a" * 32, db_path=str(tmp_path / "test.db"))
    )
    meter_id, tariff = "electricity:1234567890123:ABC", "E-1R-VAR-22-11-01-A"
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "a" * 32
        app.state.store.save_meters(
            [
                {
                    "id": meter_id,
                    "fuel": "electricity",
                    "meter_point": "1234567890123",
                    "serial_number": "ABC",
                    "active": True,
                    "gas_unit": "kwh",
                    "is_export": False,
                    "agreements": [
                        {
                            "tariff_code": tariff,
                            "valid_from": "2026-01-01T00:00:00Z",
                            "valid_to": None,
                        }
                    ],
                }
            ]
        )
        app.state.store.upsert_consumption(
            [
                {
                    "meter_id": meter_id,
                    "interval_start": "2026-01-01T00:00:00Z",
                    "interval_end": "2026-01-01T00:30:00Z",
                    "consumption": 0.5,
                }
            ]
        )
        app.state.store.upsert_rates(
            [
                {
                    "meter_id": meter_id,
                    "tariff_code": tariff,
                    "kind": kind,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": None,
                    "value_inc_vat": value,
                    "payment_method": "DIRECT_DEBIT",
                }
                for kind, value in (("unit", 25), ("standing", 50))
            ]
        )
        response = client.get(
            "/v1/analytics",
            params={
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:30:00Z",
                "meter_id": meter_id,
            },
        )
        assert response.status_code == 200
        report = response.json()
        assert report["totals"]["import_kwh"] == 0.5
        assert report["quality"]["complete"] is True
        assert abs(report["totals"]["net_cost_gbp"] - (0.5 * 25 / 100 + 50 / 100 / 48)) < 1e-9
        assert client.get("/v1/analytics", params={"meter_id": "unknown"}).status_code == 404


def test_private_responses_are_not_cacheable_and_bad_tokens_do_not_leak(tmp_path):
    from octopus_service.api import create_app

    app = create_app(
        Settings(_env_file=None, service_token="a" * 32, db_path=str(tmp_path / "test.db"))
    )
    with TestClient(app) as client:
        response = client.get("/v1/status", headers={"Authorization": "Bearer " + "b" * 32})
        assert response.status_code == 401
        assert "b" * 32 not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_non_ascii_configured_token_does_not_crash_authentication(tmp_path):
    from octopus_service.api import create_app

    app = create_app(
        Settings(_env_file=None, service_token="é" * 32, db_path=str(tmp_path / "test.db"))
    )
    with TestClient(app) as client:
        assert (
            client.get("/v1/status", headers={"Authorization": "Bearer invalid"}).status_code == 401
        )


def test_consumption_rates_csv_validate_ranges_and_pagination(tmp_path):
    from octopus_service.api import create_app

    app = create_app(
        Settings(_env_file=None, service_token="a" * 32, db_path=str(tmp_path / "test.db"))
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "a" * 32
        app.state.store.upsert_consumption(
            [
                {
                    "meter_id": "electricity:123:ABC",
                    "interval_start": "2026-01-01T00:00:00Z",
                    "interval_end": "2026-01-01T00:30:00Z",
                    "consumption": 0.125,
                },
                {
                    "meter_id": "electricity:123:ABC",
                    "interval_start": "2026-01-01T00:30:00Z",
                    "interval_end": "2026-01-01T01:00:00Z",
                    "consumption": 0.5,
                },
            ]
        )
        params = {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"}
        response = client.get("/v1/consumption", params={**params, "limit": 1})
        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert len(response.json()["results"]) == 1
        assert response.json()["next_offset"] == 1
        assert client.get("/v1/rates", params=params).json()["count"] == 0
        csv = client.get("/v1/export", params=params)
        assert csv.status_code == 200
        assert "text/csv" in csv.headers["content-type"]
        assert "0.125" in csv.text
        for overrides in (
            {"start": "2026-01-01T00:00:00"},
            {"end": "2025-01-01T00:00:00Z"},
            {"limit": 0},
            {"start": "2020-01-01T00:00:00Z"},
            {"meter_id": "unknown"},
        ):
            assert client.get("/v1/consumption", params={**params, **overrides}).status_code in (
                404,
                422,
            )
