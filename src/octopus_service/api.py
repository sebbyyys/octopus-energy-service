"""Private REST API. Create via uvicorn octopus_service.api:create_app --factory."""

import csv
import io
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from octopus_service.settings import Settings
from octopus_service.snapshot import build_snapshot
from octopus_service.storage import Store
from octopus_service.sync import SyncBusyError, SyncManager


def create_app(settings: Settings | None = None, client_factory=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(settings.db_path)
        app.state.sync = SyncManager(app.state.store, settings, client_factory)
        app.state.sync.launch_scheduler()
        try:
            yield
        finally:
            await app.state.sync.close()
            app.state.store.close()

    app = FastAPI(
        title="Octopus Energy Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def private_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), settings.service_token.get_secret_value().encode()
        ):
            raise HTTPException(
                401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"}
            )

    router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @router.get("/status")
    def status():
        return {
            "configured": bool(settings.account_number),
            "sync_running": app.state.sync.running,
            "sync": app.state.store.get_meta("sync"),
        }

    @router.get("/meters")
    def meters():
        return {"meters": app.state.store.meters()}

    @router.get("/openapi.json", include_in_schema=False)
    def schema():
        return app.openapi()

    @router.get("/home-assistant")
    def home_assistant():
        return build_snapshot(app.state.store, settings)

    @router.post("/sync", status_code=202)
    async def sync(start: datetime | None = None, end: datetime | None = None):
        if not settings.account_number:
            raise HTTPException(503, "Octopus credentials are not configured")
        dates = period(start, end) if start is not None or end is not None else (None, None)
        try:
            app.state.sync.trigger(*dates)
        except SyncBusyError as exc:
            raise HTTPException(409, "Synchronization already in progress") from exc
        except ValueError as exc:
            raise HTTPException(422, "Invalid synchronization period") from exc
        return {"status": "accepted", "status_url": "/v1/status"}

    def period(start: datetime | None = None, end: datetime | None = None):
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=30)
        if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
            raise HTTPException(422, "start and end must include timezone offsets")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end <= start or end - start > timedelta(days=730):
            raise HTTPException(422, "range must be positive and no longer than 730 days")
        return start, end

    def check_meter(meter_id: str | None):
        if meter_id is not None and meter_id not in {m["id"] for m in app.state.store.meters()}:
            raise HTTPException(404, "Meter not found")

    def paginated(kind, dates, meter_id, limit, offset):
        check_meter(meter_id)
        records = getattr(app.state.store, kind)(*dates, meter_id)
        return {
            "count": len(records),
            "results": records[offset : offset + limit],
            "next_offset": offset + limit if offset + limit < len(records) else None,
        }

    @router.get("/consumption")
    def consumption(
        dates: Annotated[tuple, Depends(period)],
        meter_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return paginated("consumption", dates, meter_id, limit, offset)

    @router.get("/rates")
    def rates(
        dates: Annotated[tuple, Depends(period)],
        meter_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return paginated("rates", dates, meter_id, limit, offset)

    @router.get("/analytics")
    def analytics(dates: Annotated[tuple, Depends(period)], meter_id: str | None = None):
        from octopus_service.analytics import analyze

        check_meter(meter_id)
        selected = [m for m in app.state.store.meters() if meter_id is None or m["id"] == meter_id]
        for meter in selected:
            if meter["id"] in settings.gas_units_json:
                meter["gas_unit"] = settings.gas_units_json[meter["id"]]
        return analyze(
            selected,
            app.state.store.consumption(*dates, meter_id),
            app.state.store.rates(*dates, meter_id),
            *dates,
            timezone=settings.timezone,
            payment_method=settings.payment_method,
            calorific_value=settings.calorific_value,
            correction_factor=settings.correction_factor,
        )

    @router.get("/export")
    def export(dates: Annotated[tuple, Depends(period)], meter_id: str | None = None):
        check_meter(meter_id)
        records = app.state.store.consumption(*dates, meter_id)
        if len(records) > 100000:
            raise HTTPException(413, "Export exceeds 100000 rows; request a smaller period")
        output = io.StringIO()
        writer = csv.DictWriter(
            output, fieldnames=("meter_id", "interval_start", "interval_end", "consumption")
        )
        writer.writeheader()
        writer.writerows(records)
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="consumption.csv"'},
        )

    app.include_router(router)
    return app
