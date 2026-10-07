import pytest
from pydantic import ValidationError


def test_settings_require_strong_service_token():
    from octopus_service.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, service_token="short")
    settings = Settings(_env_file=None, service_token="a" * 32)
    assert settings.timezone == "Europe/London"
    assert settings.sync_interval_seconds == 1800
    assert settings.service_token.get_secret_value() == "a" * 32
    assert "a" * 32 not in repr(settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": "private-key"},
        {"account_number": "A-12345678"},
        {"timezone": "Not/AZone"},
        {"sync_interval_seconds": 10},
        {"initial_days": 731},
        {"lookback_days": 0},
        {"payment_method": "WRONG"},
        {"gas_units_json": {"gas:x:y": "guess"}},
        {"calorific_value": float("nan")},
        {"correction_factor": 0},
    ],
)
def test_settings_reject_invalid_configuration(overrides):
    from octopus_service.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, service_token="a" * 32, **overrides)


def test_validation_errors_do_not_reveal_credentials():
    from octopus_service.settings import Settings

    secret = "sensitive-private-api-key"
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, service_token="a" * 32, api_key=secret)
    assert secret not in str(error.value)
