"""Exercise the real HTTP listener, not only an in-process ASGI transport."""

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def test_uvicorn_factory_serves_authenticated_api_and_shuts_down(tmp_path):
    token = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "OCTOPUS_SERVICE_TOKEN": token,
        "OCTOPUS_API_KEY": "",
        "OCTOPUS_ACCOUNT_NUMBER": "",
        "OCTOPUS_DB_PATH": str(tmp_path / "real-http.sqlite3"),
    }
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with (tmp_path / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "octopus_service.api:create_app",
                    "--factory",
                    "--fd",
                    str(listener.fileno()),
                    "--workers",
                    "1",
                    "--log-level",
                    "info",
                ],
                cwd=tmp_path,
                env=env,
                pass_fds=(listener.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}", timeout=1, trust_env=False
                ) as client:
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            response = client.get("/health/live")
                            if response.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() >= deadline or process.poll() is not None:
                            log.seek(0)
                            raise AssertionError("Server failed to become ready: " + log.read())
                        time.sleep(0.05)
                    assert response.json() == {"status": "ok"}
                    assert client.get("/v1/status").status_code == 401
                    client.headers["Authorization"] = "Bearer " + token
                    assert client.get("/v1/status").json()["configured"] is False
                    assert client.get("/v1/home-assistant").json()["available"] is False
                    assert client.get("/v1/openapi.json").json()["openapi"].startswith("3.")
                    assert client.get("/v1/export").text.startswith("meter_id,interval_start")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            # Uvicorn restores/re-raises SIGTERM after its lifespan shutdown.
            assert process.returncode in (0, -signal.SIGTERM)
            log.seek(0)
            assert "Application shutdown complete" in log.read()
