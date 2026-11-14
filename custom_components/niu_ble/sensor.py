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
from homeassistant.util import dt as dt_util

from . import rides as R
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
    entities.append(NiuLogbookSensor(coordinator))
    entities.append(NiuLastTripSensor(coordinator))
    entities.extend(NiuRideSensor(coordinator, desc) for desc in RIDE_SENSORS)
    entities.append(NiuCloudSyncSensor(coordinator))
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


ATTR_RIDES = 50


def _last(field: str) -> Callable[[NiuCoordinator], Any]:
    def value(coordinator: NiuCoordinator) -> Any:
        rides = coordinator.ride_list
        return rides[0].get(field) if rides else None

    return value


def _average(days: int | None) -> Callable[[NiuCoordinator], float | None]:
    return lambda coordinator: R.average_rate(coordinator.ride_list, dt_util.utcnow(), days)


@dataclass(frozen=True, kw_only=True)
class NiuRideSensorDescription(SensorEntityDescription):
    """Beschreibung eines aus den Cloud-Fahrten abgeleiteten Sensors."""

    value_fn: Callable[[NiuCoordinator], Any]


RIDE_SENSORS: tuple[NiuRideSensorDescription, ...] = (
    NiuRideSensorDescription(
        key="consumption_pct_per_km",
        icon="mdi:battery-arrow-down",
        native_unit_of_measurement="%/km",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_last("prozent_pro_km"),
    ),
    NiuRideSensorDescription(
        key="consumption_wh_per_km",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement="Wh/km",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_last("wh_pro_km"),
    ),
    NiuRideSensorDescription(
        key="consumption_7d",
        icon="mdi:chart-line",
        native_unit_of_measurement="%/km",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_average(7),
    ),
    NiuRideSensorDescription(
        key="consumption_30d",
        icon="mdi:chart-line",
        native_unit_of_measurement="%/km",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_average(30),
    ),
    NiuRideSensorDescription(
        key="consumption_total",
        icon="mdi:chart-line",
        native_unit_of_measurement="%/km",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_average(None),
    ),
    NiuRideSensorDescription(
        key="ridden_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda coordinator: R.total_km(coordinator.ride_list)
        if coordinator.ride_list
        else None,
    ),
)


class NiuRideSensor(NiuEntity, SensorEntity):
    """Kennzahl aus den Cloud-Fahrten."""

    entity_description: NiuRideSensorDescription

    def __init__(self, coordinator: NiuCoordinator, description: NiuRideSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Aktueller Wert."""
        return self.entity_description.value_fn(self.coordinator)


class NiuLogbookSensor(NiuEntity, SensorEntity):
    """Fahrtenbuch: Anzahl der Fahrten, die neuesten im Attribut `fahrten`."""

    _attr_icon = "mdi:book-open-variant"
    _unrecorded_attributes = frozenset({"fahrten"})

    def __init__(self, coordinator: NiuCoordinator) -> None:
        super().__init__(coordinator, "logbook")

    @property
    def native_value(self) -> int | None:
        """Anzahl bekannter Fahrten."""
        if not self.coordinator.cloud_enabled and not self.coordinator.ride_list:
            return None
        return len(self.coordinator.ride_list)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Neueste Fahrten zuerst."""
        return {"fahrten": self.coordinator.ride_list[:ATTR_RIDES]}


class NiuLastTripSensor(NiuEntity, SensorEntity):
    """Akkuverbrauch der letzten Fahrt in Prozentpunkten."""

    _attr_icon = "mdi:battery-minus-variant"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NiuCoordinator) -> None:
        super().__init__(coordinator, "last_trip_consumption")

    @property
    def native_value(self) -> int | None:
        """Verbrauch der letzten Fahrt."""
        rides = self.coordinator.ride_list
        return rides[0].get("verbrauch") if rides else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Details der letzten Fahrt."""
        rides = self.coordinator.ride_list
        return {k: v for k, v in rides[0].items() if k != "id"} if rides else None


class NiuCloudSyncSensor(NiuEntity, SensorEntity):
    """Zeitpunkt des letzten erfolgreichen Cloud-Abrufs."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: NiuCoordinator) -> None:
        super().__init__(coordinator, "cloud_sync")

    @property
    def native_value(self) -> datetime | None:
        """Letzter erfolgreicher Abruf der Fahrtenliste."""
        return self.coordinator.cloud_last_sync

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Letzte Fehlermeldung, falls der Abruf scheitert."""
        return {"fehler": self.coordinator.cloud_error}
