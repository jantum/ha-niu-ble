"""Koordinator: pollt den Roller per Kurzverbindung und merkt sich die letzten Werte."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client import async_read_scooter
from .const import (
    CONF_BLE_AES,
    CONF_BLE_PASSWORD,
    CONF_INTERVAL,
    DEFAULT_INTERVAL_MIN,
    DOMAIN,
    GROUP_FIRMWARE,
    GROUP_RIDE,
    GROUP_STATE,
    POLL_TIMEOUT,
)
from .protocol import NiuError

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type NiuConfigEntry = ConfigEntry[NiuCoordinator]


def storage_key(entry_id: str) -> str:
    """Dateiname des Wertespeichers."""
    return f"{DOMAIN}.{entry_id}"


class NiuCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Hält die zuletzt gelesenen Werte, auch wenn der Roller unterwegs ist."""

    config_entry: NiuConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NiuConfigEntry) -> None:
        interval = entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL_MIN)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=timedelta(minutes=interval),
        )
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self._password: str = entry.data[CONF_BLE_PASSWORD]
        self._aes: str = entry.data.get(CONF_BLE_AES, "")
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._lock = asyncio.Lock()
        self._firmware_read = False
        self.in_range = False
        self.last_success: datetime | None = None
        self.data = {}

    async def async_load(self) -> None:
        """Gespeicherte Werte laden und Bluetooth-Callbacks registrieren."""
        if stored := await self._store.async_load():
            self.data = stored.get("values", {})
            if ts := stored.get("last_success"):
                self.last_success = dt_util.parse_datetime(ts)

        self.in_range = bluetooth.async_address_present(self.hass, self.address, connectable=True)
        entry = self.config_entry
        entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._async_on_advertisement,
                BluetoothCallbackMatcher(address=self.address, connectable=True),
                BluetoothScanningMode.ACTIVE,
            )
        )
        entry.async_on_unload(
            bluetooth.async_track_unavailable(
                self.hass, self._async_on_unavailable, self.address, connectable=True
            )
        )

    @callback
    def _async_on_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        if self.in_range:
            return
        self.in_range = True
        _LOGGER.debug("Roller %s wieder in Reichweite", self.address)
        self.async_update_listeners()
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _async_on_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        if not self.in_range:
            return
        self.in_range = False
        _LOGGER.debug("Roller %s außer Reichweite", self.address)
        self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        """Roller lesen. Ist er nicht erreichbar, bleiben die alten Werte stehen."""
        current = dict(self.data or {})
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            return current

        groups = [GROUP_STATE, GROUP_RIDE]
        # Firmware-Stände einmal je HA-Start lesen.
        if not self._firmware_read:
            groups.append(GROUP_FIRMWARE)

        async with self._lock:
            try:
                async with asyncio.timeout(POLL_TIMEOUT):
                    values = await async_read_scooter(device, self._password, self._aes, groups)
            except (BleakError, NiuError, TimeoutError) as err:
                _LOGGER.debug("Roller %s nicht lesbar: %s", self.address, err)
                return current

        if not values:
            return current
        if "db_k_sw_ver" in values:
            self._firmware_read = True
        current.update(values)
        self.last_success = dt_util.utcnow()
        await self._store.async_save(
            {"values": current, "last_success": self.last_success.isoformat()}
        )
        return current
