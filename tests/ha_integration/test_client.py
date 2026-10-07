"""Exercise the standalone client against real aiohttp HTTP servers."""

import importlib

import pytest

pytest.importorskip("homeassistant")
pytestmark = pytest.mark.usefixtures("socket_enabled")
from aiohttp import ClientSession, web  # noqa: E402

MODULE = "custom_components.octopus_energy_service.api"


@pytest.mark.asyncio
async def test_fetch_authenticated_snapshot(aiohttp_server):
    seen = []

    async def snapshot(request):
        seen.append((request.path, request.headers.get("Authorization")))
        return web.json_response({"available": False, "stale": True})

    app = web.Application()
    app.router.add_get("/v1/home-assistant", snapshot)
    server = await aiohttp_server(app)
    api = importlib.import_module(MODULE)
    async with ClientSession() as session:
        client = api.ServiceClient(session, str(server.make_url("/")), "test-token")
        assert await client.async_snapshot() == {"available": False, "stale": True}
    assert seen == [("/v1/home-assistant", "Bearer test-token")]


@pytest.mark.parametrize(
    "url",
    [
        "http://user:password@localhost",
        "http://localhost?token=x",
        "http://localhost#x",
        "http://localhost?",
        "http://localhost#",
        "http://localhost/v1/home-assistant",
        "ftp://localhost",
        "http://",
        "http://localhost:bad",
        "http://localhost\\evil",
        "http://localhost/../",
        "http://localhost\n",
        "http://localhost/%2e%2e/",
    ],
)
def test_reject_unsafe_or_nonroot_url(url):
    api = importlib.import_module(MODULE)
    with pytest.raises(ValueError):
        api.normalize_base_url(url)


def test_canonical_root_url():
    api = importlib.import_module(MODULE)
    assert api.normalize_base_url("HTTP://LOCALHOST:80/") == "http://localhost"
    assert api.normalize_base_url("https://[::1]:8443/") == "https://[::1]:8443"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 404, 429, 500])
async def test_reject_http_error_without_following_redirects(aiohttp_server, status):
    forwarded = []

    async def sink(request):
        forwarded.append(request.headers.get("Authorization"))
        return web.json_response({"available": True, "stale": False})

    foreign = web.Application()
    foreign.router.add_get("/sink", sink)
    target = await aiohttp_server(foreign)

    async def response(request):
        return web.Response(
            status=status,
            headers={"Location": str(target.make_url("/sink"))},
            text="secret-token-private-meter",
        )

    app = web.Application()
    app.router.add_get("/v1/home-assistant", response)
    server = await aiohttp_server(app)
    api = importlib.import_module(MODULE)
    async with ClientSession() as session:
        client = api.ServiceClient(session, str(server.make_url("/")), "secret-token")
        error = api.ServiceAuthError if status in (401, 403) else api.ServiceError
        with pytest.raises(error) as caught:
            await client.async_snapshot()
    assert "secret" not in str(caught.value)
    assert "meter" not in str(caught.value)
    assert forwarded == []


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        "null",
        "{}",
        '{"available":"yes","stale":false}',
        '{"available":true,"stale":0}',
    ],
)
async def test_reject_invalid_snapshot_envelope(aiohttp_server, body):
    async def response(request):
        return web.Response(text=body, content_type="application/json")

    app = web.Application()
    app.router.add_get("/v1/home-assistant", response)
    server = await aiohttp_server(app)
    api = importlib.import_module(MODULE)
    async with ClientSession() as session:
        with pytest.raises(api.ServiceError):
            await api.ServiceClient(session, str(server.make_url("/")), "token").async_snapshot()


@pytest.mark.parametrize("token", ["", " ", "bad\nTOKEN", "bad\rTOKEN", "bad\x00TOKEN", None])
async def test_reject_unsafe_token_before_any_request(token):
    api = importlib.import_module(MODULE)
    async with ClientSession() as session:
        with pytest.raises(api.ServiceAuthError) as caught:
            api.ServiceClient(session, "http://127.0.0.1:9999", token)
    assert "TOKEN" not in str(caught.value)
