"""Distribution layout and translation checks (also runnable without HA)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "octopus_energy_service"


def test_hacs_distribution_manifest():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert hacs["homeassistant"] == "2026.9.4"
    assert hacs.get("content_in_root", False) is False
    assert manifest["domain"] == "octopus_energy_service"
    assert manifest["version"] == "0.2.0"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "service"
    assert manifest["iot_class"] == "local_polling"
    assert manifest["requirements"] == []
    assert manifest["codeowners"] == ["@sebbyyys"]
    assert (COMPONENT / "brand" / "icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_english_translations_cover_steps_and_errors():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert english == strings
    assert set(strings["config"]["step"]) == {"user", "reauth_confirm", "reconfigure"}
    assert {"invalid_auth", "invalid_url", "cannot_connect", "already_configured"} <= set(
        strings["config"]["error"]
    )
    assert {"already_configured", "reauth_successful", "reconfigure_successful"} <= set(
        strings["config"]["abort"]
    )
