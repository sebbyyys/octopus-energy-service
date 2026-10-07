"""Config flows executed by installed Home Assistant, not fake modules."""

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
from aiohttp import web  # noqa: E402
from homeassistant import data_entry_flow  # noqa: E402

DOMAIN = "octopus_energy_service"
pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_user_flow_validates_protected_endpoint_and_masks_token(hass, aiohttp_server):
    seen = []

    async def snapshot(request):
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"available": False, "stale": True})

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert form["type"] == data_entry_flow.FlowResultType.FORM
    token_selector = next(v for k, v in form["data_schema"].schema.items() if k.schema == "token")
    assert token_selector.config["type"] == "password"
    with patch(
        "homeassistant.config_entries.ConfigEntry.async_setup", new=AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                "base_url": str(server.make_url("/")),
                "token": "test-token",
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "base_url": str(server.make_url("/")).rstrip("/"),
        "token": "test-token",
    }
    assert seen == ["Bearer test-token"]
    assert result["result"].unique_id is None  # A mutable URL is not a device identity.


async def test_duplicate_canonical_url_aborts_without_network(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN, data={"base_url": "http://localhost", "token": "existing"}
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={"base_url": "HTTP://LOCALHOST:80/", "token": "new"},
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauthentication_updates_same_entry_and_never_prefills_secret(hass, aiohttp_server):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    async def snapshot(request):
        return web.json_response(
            {"available": False, "stale": True},
            status=200 if request.headers.get("Authorization") == "Bearer new-token" else 401,
        )

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"base_url": str(server.make_url("/")).rstrip("/"), "token": "old-token"},
    )
    entry.add_to_hass(hass)
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
    )
    assert form["step_id"] == "reauth_confirm"
    assert "old-token" not in repr(form["data_schema"].schema)
    bad = await hass.config_entries.flow.async_configure(form["flow_id"], {"token": "wrong"})
    assert bad["errors"] == {"base": "invalid_auth"}
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"token": "new-token"}
        )
        await hass.async_block_till_done()
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"] == "new-token"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    reload.assert_awaited_once_with(entry.entry_id)


async def test_reconfigure_preserves_entity_identity_and_rejects_duplicate(hass, aiohttp_server):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    async def snapshot(request):
        return web.json_response({"available": False, "stale": True})

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    entry = MockConfigEntry(
        domain=DOMAIN, data={"base_url": "http://old-host", "token": "old-secret"}
    )
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, data={"base_url": "http://taken-host", "token": "other"})
    other.add_to_hass(hass)
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    assert form["step_id"] == "reconfigure"
    assert "old-secret" not in repr(form["data_schema"].schema)
    bad = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"base_url": "http://taken-host/", "token": "new"}
    )
    assert bad["errors"] == {"base": "already_configured"}
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                "base_url": str(server.make_url("/")),
                "token": "new-token",
            },
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"base_url": str(server.make_url("/")).rstrip("/"), "token": "new-token"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


@pytest.mark.parametrize("source", ["user", "reauth", "reconfigure"])
@pytest.mark.parametrize("status, expected", [(401, "invalid_auth"), (500, "cannot_connect")])
async def test_flow_reports_sanitized_api_errors(hass, aiohttp_server, source, status, expected):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    async def snapshot(request):
        return web.Response(status=status, text="PRIVATE-BODY-TOKEN")

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    data = {"base_url": str(server.make_url("/")), "token": "test-token"}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    if source != "user":
        entry.add_to_hass(hass)
    context = {"source": source, "entry_id": entry.entry_id}
    form = await hass.config_entries.flow.async_init(DOMAIN, context=context)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"token": data["token"]} if source == "reauth" else data,
    )
    assert result["errors"] == {"base": expected}
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize("source", ["user", "reconfigure"])
async def test_flow_rejects_unsafe_url(hass, source):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={"base_url": "http://safe-host", "token": "old"})
    if source != "user":
        entry.add_to_hass(hass)
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"base_url": "http://user:password@unsafe", "token": "test-token"}
    )
    assert result["errors"] == {"base": "invalid_url"}


async def test_concurrent_user_flows_do_not_create_duplicate_entries(hass, aiohttp_server):
    import asyncio

    gate = asyncio.Event()
    calls = 0

    async def snapshot(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            gate.set()
        await gate.wait()
        return web.json_response({"available": False, "stale": True})

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    data = {"base_url": str(server.make_url("/")), "token": "test-token"}
    with patch(
        "homeassistant.config_entries.ConfigEntry.async_setup", new=AsyncMock(return_value=True)
    ):
        results = await asyncio.gather(
            *(
                hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data=data)
                for _ in range(2)
            )
        )
    assert sorted(result["type"] for result in results) == [
        data_entry_flow.FlowResultType.ABORT,
        data_entry_flow.FlowResultType.CREATE_ENTRY,
    ]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
