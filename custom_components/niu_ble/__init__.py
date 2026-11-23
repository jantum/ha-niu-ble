"""NIU KQi über Bluetooth LE (nur lesend)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .coordinator import STORAGE_VERSION, NiuConfigEntry, NiuCoordinator, storage_key

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: NiuConfigEntry) -> bool:
    """Eintrag einrichten."""
    coordinator = NiuCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    # Erster Abruf im Hintergrund, damit der HA-Start nicht auf Bluetooth wartet.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), name="niu_ble first refresh"
    )
    if coordinator.cloud_enabled:
        entry.async_create_background_task(
            hass, coordinator.async_sync_rides(), name="niu_ble cloud sync"
        )
    return True


async def _async_reload(hass: HomeAssistant, entry: NiuConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NiuConfigEntry) -> bool:
    """Eintrag entladen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NiuConfigEntry) -> None:
    """Gespeicherte Werte beim Löschen des Eintrags entfernen."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
