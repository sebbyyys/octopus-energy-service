"""Offline app wrapper contract tests; no Supervisor or supplier credentials."""

import importlib.util
import json
from pathlib import Path

import pytest

from octopus_service.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addons" / "octopus_energy_service"
TOKEN = "test-only-service-token-" + "x" * 32


def entrypoint():
    path = ADDON / "entrypoint.py"
    assert path.is_file(), "The app needs a self-contained entrypoint"
    spec = importlib.util.spec_from_file_location("addon_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_options_produce_complete_backend_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOPUS_API_KEY", "must-not-inherit")
    env = entrypoint().options_environment({"service_token": TOKEN}, tmp_path)
    assert env["OCTOPUS_SERVICE_TOKEN"] == TOKEN
    assert env["OCTOPUS_API_KEY"] == env["OCTOPUS_ACCOUNT_NUMBER"] == ""
    assert env["OCTOPUS_DB_PATH"] == str(tmp_path / "history" / "octopus.sqlite3")
    assert env["OCTOPUS_TIMEZONE"] == "Europe/London"
    assert env["OCTOPUS_SYNC_INTERVAL_SECONDS"] == "1800"
    assert env["OCTOPUS_INITIAL_DAYS"] == "90"
    assert env["OCTOPUS_LOOKBACK_DAYS"] == "7"
    assert env["OCTOPUS_PAYMENT_METHOD"] == "DIRECT_DEBIT"
    assert json.loads(env["OCTOPUS_GAS_UNITS_JSON"]) == {}


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"service_token": "short-secret"},
        {"service_token": 123},
        {"api_key": "private-key"},
        {"account_number": "A-PRIVATE"},
        {"timezone": "private-not-a-zone"},
        {"sync_interval_seconds": 299},
        {"sync_interval_seconds": True},
        {"initial_days": 731},
        {"lookback_days": 0},
        {"payment_method": "private-invalid"},
        {"calorific_value": float("nan")},
        {"correction_factor": 0},
        {"db_path": "private-path"},
        {"gas_units": [{"meter_id": "gas:private:meter", "unit": "litres"}]},
        {"gas_units": [{"meter_id": "gas:private:meter", "unit": "m3"}] * 2},
        {"gas_units": "private-json"},
        {"gas_units": [{"unit": "m3"}]},
        {"gas_units": [{"meter_id": "", "unit": "m3"}]},
    ],
)
def test_invalid_options_fail_without_echoing_values(options, tmp_path):
    module = entrypoint()
    configured = {"service_token": TOKEN} | options
    if not options:
        configured = {}
    with pytest.raises(module.ConfigurationError) as error:
        module.options_environment(configured, tmp_path)
    assert "private" not in str(error.value)
    assert TOKEN not in str(error.value)
    assert "short-secret" not in str(error.value)


def test_gas_mapping_and_paired_credentials_are_backend_compatible(tmp_path, monkeypatch):
    configured = {
        "service_token": TOKEN,
        "api_key": "fixture-key",
        "account_number": "A-123",
        "gas_units": [
            {"meter_id": "gas:123:ABC", "unit": "m3"},
            {"meter_id": "gas:456:DEF", "unit": "kwh"},
        ],
    }
    env = entrypoint().options_environment(configured, tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.gas_units_json == {"gas:123:ABC": "m3", "gas:456:DEF": "kwh"}
    assert settings.api_key.get_secret_value() == "fixture-key"
    assert settings.account_number == "A-123"


def test_history_permissions_and_nonroot_support(tmp_path):
    import os
    import stat

    module = entrypoint()
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"service_token": TOKEN}))
    history = tmp_path / "history"
    history.mkdir()
    db = history / "octopus.sqlite3"
    db.write_text("fixture")
    module.prepare_data(tmp_path, uid=os.geteuid(), gid=os.getegid())
    assert stat.S_IMODE(options.stat().st_mode) == 0o600
    assert stat.S_IMODE(history.stat().st_mode) == 0o700
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert (db.stat().st_uid, db.stat().st_gid) == (os.geteuid(), os.getegid())


def test_history_symlinks_are_rejected_without_touching_target(tmp_path):
    module = entrypoint()
    (tmp_path / "options.json").write_text("{}")
    history = tmp_path / "history"
    history.mkdir()
    target = tmp_path / "outside"
    target.write_text("unchanged")
    (history / "octopus.sqlite3").symlink_to(target)
    with pytest.raises(module.ConfigurationError, match="History must contain"):
        module.prepare_data(tmp_path, uid=module.os.geteuid(), gid=module.os.getegid())
    assert target.read_text() == "unchanged"


def test_launch_drops_privileges_before_secret_free_exec(tmp_path, monkeypatch):
    module = entrypoint()
    (tmp_path / "options.json").write_text(json.dumps({"service_token": TOKEN}))
    monkeypatch.setenv("OCTOPUS_UNEXPECTED", "private-inherited")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "private-supervisor")
    events = []
    monkeypatch.setattr(module, "prepare_data", lambda *args, **kwargs: events.append("prepare"))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "setgroups", lambda groups: events.append(("groups", groups)))
    monkeypatch.setattr(module.os, "setgid", lambda gid: events.append(("gid", gid)))
    monkeypatch.setattr(module.os, "setuid", lambda uid: events.append(("uid", uid)))

    def execve(executable, args, env):
        events.append("exec")
        assert executable == "/fixture/python"
        assert args == [
            executable,
            "-m",
            "uvicorn",
            "octopus_service.api:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--workers",
            "1",
            "--no-access-log",
        ]
        assert TOKEN not in " ".join(args)
        assert env["OCTOPUS_SERVICE_TOKEN"] == TOKEN
        assert "OCTOPUS_UNEXPECTED" not in env
        assert "SUPERVISOR_TOKEN" not in env

    monkeypatch.setattr(module.os, "execve", execve)
    module.launch(tmp_path, python="/fixture/python")
    assert events == ["prepare", ("groups", []), ("gid", 10001), ("uid", 10001), "exec"]


@pytest.mark.parametrize("content", ["{private-broken", "[]", '{"service_token":"private-short"}'])
def test_startup_errors_are_sanitized(content, tmp_path, capsys):
    module = entrypoint()
    (tmp_path / "options.json").write_text(content)
    assert module.main(tmp_path) == 1
    captured = capsys.readouterr()
    assert "private" not in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_root_setup_assigns_only_history_to_service_uid(tmp_path, monkeypatch):
    module = entrypoint()
    options = tmp_path / "options.json"
    options.write_text("{}")
    history = tmp_path / "history"
    history.mkdir()
    db = history / "octopus.sqlite3"
    db.write_text("fixture")
    owners = []
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda path, uid, gid: owners.append((path, uid, gid)))
    module.prepare_data(tmp_path)
    assert (options, 0, 0) in owners
    assert (history, 10001, 10001) in owners
    assert (db, 10001, 10001) in owners
    assert (tmp_path, -1, 10001) in owners
    assert tmp_path.stat().st_mode & 0o777 == 0o710


def test_entrypoint_real_exec_serves_api_and_handles_sigterm(tmp_path):
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time

    import httpx

    (tmp_path / "options.json").write_text(json.dumps({"service_token": TOKEN}))
    # Only adapt filesystem ownership and the listening port for a safe non-root test.
    # Production options loading, validation, environment, exec and uvicorn are real.
    script = tmp_path / "run-wrapper.py"
    script.write_text("""
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("wrapper", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prepare = module.prepare_data
module.prepare_data = lambda data: prepare(data, uid=os.geteuid(), gid=os.getegid())
execute = os.execve
def exec_test_python(executable, args, env):
    args[args.index("--port") + 1] = sys.argv[3]
    execute(executable, args, env)
module.os.execve = exec_test_python
module.launch(Path(sys.argv[2]))
""")
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, str(script), str(ADDON / "entrypoint.py"), str(tmp_path), str(port)],
            env=env,
            cwd=tmp_path,
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
                    if process.poll() is not None or time.monotonic() >= deadline:
                        log.seek(0)
                        pytest.fail("Wrapper did not start: " + log.read())
                    time.sleep(0.05)
                assert client.get("/v1/status").status_code == 401
                response = client.get("/v1/status", headers={"Authorization": "Bearer " + TOKEN})
                assert response.json()["configured"] is False
                assert (tmp_path / "history/octopus.sqlite3").is_file()
                # exec retained the original process PID, rather than spawning a child.
                log.flush()
                log.seek(0)
                assert f"Started server process [{process.pid}]" in log.read()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert process.returncode in (0, -signal.SIGTERM)
        log.seek(0)
        output = log.read()
        assert "Application shutdown complete" in output
        assert TOKEN not in output


def test_app_manifest_has_no_host_or_api_privileges():
    import yaml

    path = ADDON / "config.yaml"
    assert path.is_file(), "Supervisor needs an app manifest"
    manifest = yaml.safe_load(path.read_text())
    assert manifest["version"] == "0.2.0"
    assert manifest["slug"] == "octopus_energy_service"
    assert set(manifest["arch"]) == {"amd64", "aarch64"}
    assert manifest["startup"] == "services"
    assert manifest["boot"] == "auto"
    assert manifest["init"] is True
    assert manifest["ports"] == {"8080/tcp": None}
    assert manifest["schema"]["service_token"] == "password"
    assert manifest["schema"]["api_key"] == "password?"
    assert not manifest["options"].get("service_token")
    for permission in (
        "host_network",
        "hassio_api",
        "homeassistant_api",
        "docker_api",
        "ingress",
        "full_access",
        "auth_api",
        "host_pid",
        "host_ipc",
        "host_dbus",
    ):
        assert not manifest.get(permission, False)
    assert not manifest.get("privileged")
    assert not manifest.get("map")
    assert manifest["backup"] == "cold"
    translations = yaml.safe_load((ADDON / "translations/en.yaml").read_text())
    assert set(translations["configuration"]) == set(manifest["schema"])
    repo = yaml.safe_load((ROOT / "repository.yaml").read_text())
    assert repo["url"] == "https://github.com/sebbyyys/octopus-energy-service"


def test_dockerfile_uses_integrity_checked_pinned_source_in_addon_context():
    path = ADDON / "Dockerfile"
    assert path.is_file(), "Supervisor needs a Dockerfile in the app context"
    dockerfile = path.read_text()
    assert "FROM python:3.13-slim" in dockerfile
    assert "install -y --no-install-recommends tzdata" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.6" in dockerfile
    assert "c32e8fcd83e90b17a8bb140a23162ab896b57f6b" in dockerfile
    assert "7ae95a19f9e0593a1332f9992a467525beda50e73d2706aeadaf63e60af68bc3" in dockerfile
    assert "sha256sum --check" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "COPY ../" not in dockerfile
    assert 'io.hass.type="app"' in dockerfile
    assert 'ENTRYPOINT ["/opt/service/.venv/bin/python", "/entrypoint.py"]' in dockerfile
