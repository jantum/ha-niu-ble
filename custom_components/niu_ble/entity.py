"""Gemeinsame Basis der NIU-BLE-Entitäten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NiuCoordinator


class NiuEntity(CoordinatorEntity[NiuCoordinator]):
    """Basisklasse: Gerätezuordnung und eindeutige ID."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NiuCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key

    @property
    def device_info(self) -> DeviceInfo:
        """Geräteeintrag des Rollers."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            manufacturer="NIU",
            model="KQi",
            name=self.coordinator.config_entry.title,
            sw_version=self.coordinator.data.get("db_k_sw_ver"),
        )
