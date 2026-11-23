"""Taster des NIU KQi."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NiuConfigEntry, NiuCoordinator
from .entity import NiuEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NiuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Taster anlegen (nur mit hinterlegtem NIU-Konto)."""
    coordinator = entry.runtime_data
    if coordinator.cloud_enabled:
        async_add_entities([NiuSyncRidesButton(coordinator)])


class NiuSyncRidesButton(NiuEntity, ButtonEntity):
    """Fahrten sofort aus der NIU-Cloud holen und den Roller neu lesen."""

    _attr_icon = "mdi:cloud-sync"

    def __init__(self, coordinator: NiuCoordinator) -> None:
        super().__init__(coordinator, "sync_rides")

    @property
    def available(self) -> bool:
        """Der Cloud-Abruf geht auch, wenn der Roller nicht in Reichweite ist."""
        return True

    async def async_press(self) -> None:
        """Cloud-Abruf, danach Bluetooth-Abruf (aktualisiert „nicht synchronisiert“)."""
        await self.coordinator.async_sync_rides()
        await self.coordinator.async_request_refresh()
        if self.coordinator.cloud_error:
            raise HomeAssistantError(
                f"NIU-Cloud-Abruf fehlgeschlagen: {self.coordinator.cloud_error}"
            )
