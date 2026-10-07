"""Small standalone authenticated local-service client."""

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL


def normalize_base_url(value: str) -> str:
    """Accept only a HTTP(S) root URL without credential-leaking components."""
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("Invalid service URL")
    if any(char in value for char in ("?", "#", "\\")):
        raise ValueError("Invalid service URL")
    try:
        parsed = urlsplit(value)
        url = URL(value)
        if (
            parsed.path not in ("", "/")
            or url.scheme not in ("http", "https")
            or not url.host
            or url.user is not None
            or url.password is not None
            or not 1 <= url.port <= 65535
        ):
            raise ValueError
    except (ValueError, TypeError) as err:
        raise ValueError("Invalid service URL") from err
    return str(url).rstrip("/")


def parse_timestamp(value: object) -> datetime | None:
    """Return aware ISO timestamps in UTC, rejecting naive or malformed values."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


class ServiceError(Exception):
    """Sanitized service/transport/payload failure."""


class ServiceAuthError(ServiceError):
    """Service token rejected."""


class ServiceClient:
    """Use a caller-owned session to retrieve the shared sensor snapshot."""

    def __init__(self, session: ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base_url = normalize_base_url(base_url)
        if (
            not isinstance(token, str)
            or not token
            or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise ServiceAuthError("Invalid service token")
        self._token = token

    async def async_snapshot(self) -> dict:
        """Fetch the auth-protected snapshot, never a public health check."""
        try:
            async with self._session.get(
                f"{self._base_url}/v1/home-assistant",
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                allow_redirects=False,
                timeout=ClientTimeout(total=20),
            ) as response:
                if response.status in (401, 403):
                    raise ServiceAuthError("Service authentication failed")
                if response.status != 200:
                    raise ServiceError("Service request failed")
                data = await response.json(loads=json.loads)
        except (TimeoutError, ClientError, ValueError, json.JSONDecodeError):
            raise ServiceError("Cannot read service snapshot") from None
        if (
            not isinstance(data, dict)
            or type(data.get("available")) is not bool
            or type(data.get("stale")) is not bool
        ):
            raise ServiceError("Invalid service snapshot")
        return data
