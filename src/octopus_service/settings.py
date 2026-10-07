"""Validated, environment-driven configuration; secrets never appear in repr."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OCTOPUS_", env_file=".env", extra="ignore")

    service_token: SecretStr
    api_key: SecretStr = SecretStr("")
    account_number: str = ""
    db_path: str = "./data/octopus.sqlite3"
    timezone: str = "Europe/London"
    sync_interval_seconds: int = Field(default=1800, ge=300, le=86400)
    initial_days: int = Field(default=90, ge=1, le=730)
    lookback_days: int = Field(default=7, ge=1, le=730)
    payment_method: Literal["DIRECT_DEBIT", "NON_DIRECT_DEBIT"] = "DIRECT_DEBIT"
    gas_units_json: dict[str, Literal["kwh", "m3"]] = Field(default_factory=dict)
    calorific_value: float = Field(default=39.2, gt=0, le=100, allow_inf_nan=False)
    correction_factor: float = Field(default=1.02264, gt=0, le=2, allow_inf_nan=False)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA name") from exc
        return value

    @model_validator(mode="after")
    def paired_credentials(self) -> "Settings":
        if bool(self.api_key.get_secret_value()) != bool(self.account_number):
            raise ValueError("API key and account number must both be configured or both omitted")
        return self

    @field_validator("service_token")
    @classmethod
    def strong_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("service token must contain at least 32 characters")
        return value
