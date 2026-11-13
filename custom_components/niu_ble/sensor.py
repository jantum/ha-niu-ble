"""Sensoren des NIU KQi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FAULTS
from .coordinator import NiuConfigEntry, NiuCoordinator
from .entity import NiuEntity


def _raw(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda data: data.get(key)


def _scaled(key: str, divisor: float) -> Callable[[dict[str, Any]], float | None]:
    def value(data: dict[str, Any]) -> float | None:
        raw = data.get(key)
        return None if raw is None else raw / divisor

    return value


def _range_km(data: dict[str, Any]) -> float | None:
    # 0 ist im Stand/beim Laden ein Platzhalter, kein Messwert.
    raw = data.get("db_k_estimated_mileage")
    return raw / 100 if raw else None


@dataclass(frozen=True, kw_only=True)
class NiuSensorDescription(SensorEntityDescription):
    """Beschreibung eines NIU-Sensors."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[NiuSensorDescription, ...] = (
    NiuSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_raw("bms_soc_rt"),
    ),
    NiuSensorDescription(
        key="estimated_range",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_range_km,
    ),
    NiuSensorDescription(
        key="ride_mode",
        value_fn=_raw("foc_k_gears"),
    ),
    NiuSensorDescription(
        key="max_speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=0,
        value_fn=_scaled("foc_k_max_speed", 10),
    ),
    NiuSensorDescription(
        key="fault_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw("db_k_f_code"),
    ),
    NiuSensorDescription(
        key="battery_capacity",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_raw("bms_bat_to_cap"),
    ),
    NiuSensorDescription(
        key="charge_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw("bms_c_cont"),
    ),
    NiuSensorDescription(
        key="firmware_display",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw("db_k_sw_ver"),
    ),
    NiuSensorDescription(
        key="firmware_controller",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw("foc_k_s_ver"),
    ),
    NiuSensorDescription(
        key="firmware_bms",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw("bms_s_ver_n"),
    ),
    NiuSensorDescription(
        key="firmware_bluetooth",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw("ecu_bt_ver"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NiuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Sensoren anlegen."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [NiuSensor(coordinator, desc) for desc in SENSORS]
    entities.append(NiuLastUpdateSensor(coordinator))
    async_add_entities(entities)


class NiuSensor(NiuEntity, SensorEntity):
    """Ein aus den Rollerdaten abgeleiteter Sensor."""

    entity_description: NiuSensorDescription

    def __init__(self, coordinator: NiuCoordinator, description: NiuSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Aktueller bzw. zuletzt gelesener Wert."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Klartext zum Fehlercode."""
        if self.entity_description.key != "fault_code":
            return None
        code = self.coordinator.data.get("db_k_f_code")
        if code is None:
            return None
        return {"description": FAULTS.get(code, f"Unbekannter Code {code}")}


class NiuLastUpdateSensor(NiuEntity, SensorEntity):
    """Zeitpunkt des letzten erfolgreichen Abrufs."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NiuCoordinator) -> None:
        super().__init__(coordinator, "last_update")

    @property
    def native_value(self) -> datetime | None:
        """Letzter erfolgreicher Abruf."""
        return self.coordinator.last_success
