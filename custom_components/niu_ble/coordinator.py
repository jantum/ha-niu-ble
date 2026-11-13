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
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client import async_read_scooter
from .const import (
    BIT_RIDES_UNSYNCED,
    CONF_BLE_AES,
    CONF_BLE_PASSWORD,
    CONF_INTERVAL,
    CONF_ODOMETER,
    CONF_TEMPERATURE,
    DEFAULT_INTERVAL_MIN,
    DOMAIN,
    GROUP_FIRMWARE,
    GROUP_RIDE,
    GROUP_STATE,
    POLL_TIMEOUT,
    RATED_VOLTAGE,
)
from .logbook import TripLog
from .protocol import NiuError

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 2

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
        self._odometer_entity: str | None = entry.options.get(CONF_ODOMETER)
        self._temperature_entity: str | None = entry.options.get(CONF_TEMPERATURE)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._lock = asyncio.Lock()
        self._firmware_read = False
        self._returned_at: datetime | None = None
        self.in_range = False
        self.last_seen: datetime | None = None
        self.last_success: datetime | None = None
        self.log = TripLog()
        self.data = {}

    # ------------------------------------------------------------------ Setup

    async def async_load(self) -> None:
        """Gespeicherte Werte laden und Callbacks registrieren."""
        if stored := await self._store.async_load():
            self.data = stored.get("values", {})
            self.log = TripLog(stored.get("logbook"))
            if ts := stored.get("last_success"):
                self.last_success = dt_util.parse_datetime(ts)

        self.in_range = bluetooth.async_address_present(self.hass, self.address, connectable=True)
        if self.in_range and self.log.open_trip is not None:
            # HA wurde neu gestartet, während der Roller unterwegs war.
            self._returned_at = dt_util.utcnow()

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
        if self._odometer_entity:
            entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, [self._odometer_entity], self._async_on_odometer
                )
            )

    # --------------------------------------------------------------- Hilfswerte

    def _float_state(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @property
    def capacity_wh(self) -> float | None:
        """Akkuenergie als Näherung: gemeldete Kapazität (mAh) x Nennspannung."""
        mah = self.data.get("bms_bat_to_cap")
        return mah * RATED_VOLTAGE / 1000 if mah else None

    @property
    def rides_unsynced(self) -> bool | None:
        """Liegen laut Roller noch nicht synchronisierte Fahrten vor?"""
        status = self.data.get("foc_k_realtime_status1")
        return None if status is None else bool(status & BIT_RIDES_UNSYNCED)

    def _payload(self) -> dict[str, Any]:
        return {
            "values": self.data,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "logbook": self.log.as_dict(),
        }

    @callback
    def _async_schedule_save(self) -> None:
        self._store.async_delay_save(self._payload, SAVE_DELAY)

    @callback
    def _async_evaluate_odometer(self) -> bool:
        """Kilometerstand prüfen; True, wenn ein Abschnitt abgeschlossen wurde."""
        segment = self.log.odometer(
            self._float_state(self._odometer_entity),
            dt_util.utcnow(),
            capacity_wh=self.capacity_wh,
            rides_unsynced=self.rides_unsynced,
            in_range=self.in_range,
        )
        if segment:
            _LOGGER.debug("Verbrauchsabschnitt abgeschlossen: %s", segment)
        return segment is not None

    # ---------------------------------------------------------------- Callbacks

    @callback
    def _async_on_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        self.last_seen = dt_util.utcnow()
        if self.in_range:
            return
        self.in_range = True
        self._returned_at = self.last_seen
        _LOGGER.debug("Roller %s wieder in Reichweite", self.address)
        self.async_update_listeners()
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _async_on_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        if not self.in_range:
            return
        self.in_range = False
        self._returned_at = None
        _LOGGER.debug("Roller %s außer Reichweite", self.address)
        self.log.leave(
            self.last_seen or dt_util.utcnow(),
            self.data.get("bms_soc_rt"),
            self._float_state(self._temperature_entity),
        )
        self._async_schedule_save()
        self.async_update_listeners()

    @callback
    def _async_on_odometer(self, event: Event[EventStateChangedData]) -> None:
        self._async_evaluate_odometer()
        self._async_schedule_save()
        self.async_update_listeners()

    # -------------------------------------------------------------------- Abruf

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
        self.data = current
        self.last_success = dt_util.utcnow()

        trip = self.log.poll(
            current.get("bms_soc_rt"),
            self._float_state(self._temperature_entity),
            self._returned_at,
        )
        if trip:
            _LOGGER.debug("Fahrt eingetragen: %s", trip)
        if self.log.open_trip is None:
            self._returned_at = None
        self._async_evaluate_odometer()

        await self._store.async_save(self._payload())
        return current
