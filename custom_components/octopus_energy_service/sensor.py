"""Native sensors mirroring the service's existing REST sensor contract."""

from math import isfinite

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import parse_timestamp
from .const import DOMAIN
from .coordinator import ServiceCoordinator

PARALLEL_UPDATES = 0


def _descriptions() -> tuple[SensorEntityDescription, ...]:
    """Build the 21 backend fields plus the status diagnostic."""
    descriptions = []
    for fuel in ("electricity", "gas", "export"):
        periods = (
            ("total", "today") if fuel == "export" else ("total", "today", "yesterday", "month")
        )
        for period in periods:
            descriptions.append(
                SensorEntityDescription(
                    key=f"{fuel}_{period}_kwh",
                    name=f"{fuel.title()} {period}",
                    device_class=SensorDeviceClass.ENERGY,
                    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                    state_class=SensorStateClass.TOTAL if period == "total" else None,
                )
            )
        if fuel != "export":
            descriptions.append(
                SensorEntityDescription(
                    key=f"{fuel}_current_rate_pence",
                    name=f"{fuel.title()} current rate",
                    native_unit_of_measurement="p/kWh",
                    state_class=SensorStateClass.MEASUREMENT,
                    icon="mdi:cash",
                )
            )
    for period in ("today", "yesterday", "week", "month", "previous_month"):
        descriptions.append(
            SensorEntityDescription(
                key=f"cost_{period}_gbp",
                name=f"Cost {period.replace('_', ' ')} estimate",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement="GBP",
            )
        )
    descriptions.extend(
        (
            SensorEntityDescription(
                key="habits_peak_hour", name="Peak hour", icon="mdi:clock-outline"
            ),
            SensorEntityDescription(
                key="habits_baseload_kw",
                name="Baseload estimate",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
            ),
            SensorEntityDescription(
                key="last_sync",
                name="Last sync",
                device_class=SensorDeviceClass.TIMESTAMP,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            SensorEntityDescription(
                key="latest_reading",
                name="Latest reading",
                device_class=SensorDeviceClass.TIMESTAMP,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            SensorEntityDescription(
                key="status",
                name="Status",
                entity_category=EntityCategory.DIAGNOSTIC,
                icon="mdi:information-outline",
            ),
        )
    )
    return tuple(descriptions)


SENSORS = _descriptions()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add every sensor to the same entry coordinator."""
    async_add_entities(
        ServiceSensor(entry.runtime_data, entry, description) for description in SENSORS
    )


class ServiceSensor(CoordinatorEntity[ServiceCoordinator], SensorEntity):
    """One field in a shared snapshot, not a separate REST poll."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ServiceCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Octopus Energy Service project",
            model="Local energy data service",
        )

    @property
    def available(self) -> bool:
        """Keep diagnostics visible for stale data; null metrics are unavailable."""
        if not super().available:
            return False
        if self.entity_description.key == "status":
            return True
        if self.entity_description.key in ("last_sync", "latest_reading"):
            return self.native_value is not None
        data = self.coordinator.data
        return (
            data.get("available") is True
            and data.get("stale") is False
            and self.native_value is not None
        )

    @property
    def extra_state_attributes(self) -> dict | None:
        """Allowlisted quality flags; supplier warning strings may identify meters."""
        if self.entity_description.key != "status":
            return None
        data = self.coordinator.data
        quality = data.get("quality")
        complete = quality.get("complete") if isinstance(quality, dict) else None
        return {
            "backend_available": data.get("available"),
            "backend_stale": data.get("stale"),
            "quality_complete": complete if type(complete) is bool else None,
        }

    @property
    def native_value(self):
        """Return native values rather than string templates."""
        data = self.coordinator.data
        key = self.entity_description.key
        if key == "status":
            return "stale" if data["stale"] else "ready" if data["available"] else "no_data"
        if key in ("last_sync", "latest_reading"):
            return parse_timestamp(data.get(key))
        group, _, field = key.partition("_")
        values = data.get(group)
        if not isinstance(values, dict):
            return None
        value = values.get(field)
        if type(value) not in (int, float):
            return None
        try:
            if not isfinite(value):
                return None
        except OverflowError:
            return None
        if key == "habits_peak_hour" and (type(value) is not int or not 0 <= value <= 23):
            return None
        return value
