"""Binärsensoren des NIU KQi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BIT_CHARGER_CONNECTED, BIT_CHARGING, BIT_RIDES_UNSYNCED
from .coordinator import NiuConfigEntry, NiuCoordinator
from .entity import NiuEntity


def _battery_bit(bit: int) -> Callable[[NiuCoordinator], bool | None]:
    def value(coordinator: NiuCoordinator) -> bool | None:
        # Unterwegs ist der Ladezustand unbekannt: kein veralteter Wert.
        if not coordinator.in_range:
            return None
        status = coordinator.data.get("bms_bat_sta_rt")
        return None if status is None else bool(status & bit)

    return value


def _rides_unsynced(coordinator: NiuCoordinator) -> bool | None:
    status = coordinator.data.get("foc_k_realtime_status1")
    return None if status is None else bool(status & BIT_RIDES_UNSYNCED)


@dataclass(frozen=True, kw_only=True)
class NiuBinarySensorDescription(BinarySensorEntityDescription):
    """Beschreibung eines NIU-Binärsensors."""

    value_fn: Callable[[NiuCoordinator], bool | None]


BINARY_SENSORS: tuple[NiuBinarySensorDescription, ...] = (
    NiuBinarySensorDescription(
        key="in_range",
        device_class=BinarySensorDeviceClass.PRESENCE,
        value_fn=lambda coordinator: coordinator.in_range,
    ),
    NiuBinarySensorDescription(
        key="charger_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=_battery_bit(BIT_CHARGER_CONNECTED),
    ),
    NiuBinarySensorDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=_battery_bit(BIT_CHARGING),
    ),
    NiuBinarySensorDescription(
        key="rides_unsynced",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_rides_unsynced,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NiuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Binärsensoren anlegen."""
    coordinator = entry.runtime_data
    async_add_entities(NiuBinarySensor(coordinator, desc) for desc in BINARY_SENSORS)


class NiuBinarySensor(NiuEntity, BinarySensorEntity):
    """Ein Binärsensor des Rollers."""

    entity_description: NiuBinarySensorDescription

    def __init__(
        self, coordinator: NiuCoordinator, description: NiuBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Zustand."""
        return self.entity_description.value_fn(self.coordinator)
