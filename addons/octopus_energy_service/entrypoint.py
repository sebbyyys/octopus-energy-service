"""Supervisor options adapter. Never print options or validation input values."""

import json
import os
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

from octopus_service.settings import Settings


class ConfigurationError(ValueError):
    """An intentionally input-free configuration diagnostic."""


DEFAULTS = {
    "api_key": "",
    "account_number": "",
    "timezone": "Europe/London",
    "sync_interval_seconds": 1800,
    "initial_days": 90,
    "lookback_days": 7,
    "payment_method": "DIRECT_DEBIT",
    "gas_units": [],
    "calorific_value": 39.2,
    "correction_factor": 1.02264,
}


def options_environment(options: dict, data_dir: Path) -> dict[str, str]:
    if not isinstance(options, dict) or set(options) - (set(DEFAULTS) | {"service_token"}):
        raise ConfigurationError("Invalid app configuration; check supported option names")
    values = DEFAULTS | options
    for key in ("service_token", "api_key", "account_number", "timezone", "payment_method"):
        if not isinstance(values.get(key), str):
            raise ConfigurationError("Invalid app configuration; check text options")
    for key in ("sync_interval_seconds", "initial_days", "lookback_days"):
        if type(values[key]) is not int:
            raise ConfigurationError("Invalid app configuration; history and sync must be integers")
    for key in ("calorific_value", "correction_factor"):
        if type(values[key]) not in (int, float):
            raise ConfigurationError("Invalid app configuration; gas factors must be numbers")
    gas = values.pop("gas_units")
    if not isinstance(gas, list):
        raise ConfigurationError("Invalid gas_units; use a list of meter_id/unit entries")
    mapping = {}
    for item in gas:
        if (
            not isinstance(item, dict)
            or set(item) != {"meter_id", "unit"}
            or not isinstance(item["meter_id"], str)
            or not item["meter_id"]
            or item["unit"] not in ("kwh", "m3")
            or item["meter_id"] in mapping
        ):
            raise ConfigurationError("Invalid gas_units; check unique meter IDs and kwh/m3 units")
        mapping[item["meter_id"]] = item["unit"]
    values["db_path"] = str(data_dir / "history" / "octopus.sqlite3")
    values["gas_units_json"] = mapping
    try:
        Settings(_env_file=None, **values)
    except ValidationError:
        raise ConfigurationError(
            "Invalid app configuration; token needs 32 characters, credentials must be paired, "
            "and timezone, history, sync, payment and gas settings must be valid"
        ) from None
    return {
        "OCTOPUS_" + key.upper(): json.dumps(value) if isinstance(value, dict) else str(value)
        for key, value in values.items()
    }


def prepare_data(data_dir: Path, *, uid: int = 10001, gid: int = 10001) -> None:
    """Only the history subtree is writable by the service; options stay private."""
    root = os.geteuid() == 0
    if not root and (os.geteuid(), os.getegid()) != (uid, gid):
        raise ConfigurationError("Run as root for setup or as the configured service UID/GID")
    options = data_dir / "options.json"
    history = data_dir / "history"
    if data_dir.is_symlink() or options.is_symlink() or history.is_symlink():
        raise ConfigurationError("Persistent data paths must not be symlinks")
    history.mkdir(mode=0o700, exist_ok=True)
    paths = [history, *history.rglob("*")]
    # Validate the entire tree before changing any file ownership or permissions.
    for path in paths:
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or info.st_nlink > 1:
            if not stat.S_ISDIR(info.st_mode):
                raise ConfigurationError("History must contain only regular files and directories")
    options.chmod(0o600)
    if root:
        os.chown(options, 0, 0)
        # Preserve the mount owner's UID, but grant only group traversal to the service.
        os.chown(data_dir, -1, gid)
        data_dir.chmod(0o710)
    for path in paths:
        path.chmod(0o700 if path.is_dir() else 0o600)
        if root:
            os.chown(path, uid, gid)


def launch(data_dir: Path = Path("/data"), *, python: str = sys.executable) -> None:
    options_path = data_dir / "options.json"
    if options_path.is_symlink():
        raise ConfigurationError("Options file must not be a symlink")
    options = json.loads(options_path.read_text())
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OCTOPUS_") and key not in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN")
    }
    env.update(options_environment(options, data_dir))
    os.umask(0o077)
    prepare_data(data_dir)
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    # Replace this process: Docker's init forwards SIGTERM straight to uvicorn.
    os.execve(
        python,
        [
            python,
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
        ],
        env,
    )


def main(data_dir: Path = Path("/data")) -> int:
    try:
        launch(data_dir)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print(
            "App startup failed; check options JSON and persistent data permissions",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
