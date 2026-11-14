"""Koordinator: pollt den Roller per Kurzverbindung, holt Fahrten aus der NIU-Cloud."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from functools import partial
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import rides as R
from .client import async_read_scooter
from .cloud import CloudError, NiuCloud
from .const import (
    BIT_RIDES_UNSYNCED,
    CONF_BLE_AES,
    CONF_BLE_PASSWORD,
    CONF_INTERVAL,
    CONF_NIU_ACCOUNT,
    CONF_NIU_PW_HASH,
    CONF_TEMPERATURE,
    DEFAULT_INTERVAL_MIN,
    DOMAIN,
    GROUP_FIRMWARE,
    GROUP_RIDE,
    GROUP_STATE,
    POLL_TIMEOUT,
    RATED_VOLTAGE,
)
from .protocol import NiuError

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# 2: Zeitstempel ohne Versatzkorrektur (0.3.1); ältere Fahrten werden neu importiert.
RIDES_VERSION = 2
CLOUD_INTERVAL = timedelta(hours=1)
CLOUD_DELAY_AFTER_SYNC = 90
RECENT_RIDE = timedelta(hours=3)
RETRY_TEMPERATURE = timedelta(days=2)

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
        self._temperature_entity: str | None = entry.options.get(CONF_TEMPERATURE)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._lock = asyncio.Lock()
        self._firmware_read = False

        self._cloud: NiuCloud | None = None
        if entry.data.get(CONF_NIU_ACCOUNT) and entry.data.get(CONF_NIU_PW_HASH):
            self._cloud = NiuCloud(
                async_get_clientsession(hass),
                entry.data[CONF_NIU_ACCOUNT],
                entry.data[CONF_NIU_PW_HASH],
            )
        self._cloud_lock = asyncio.Lock()
        self._serial: str | None = None
        self._rides: dict[str, dict[str, Any]] = {}
        self.ride_list: list[dict[str, Any]] = []
        self.cloud_last_sync: datetime | None = None
        self.cloud_error: str | None = None

        self.in_range = False
        self.last_seen: datetime | None = None
        self.last_success: datetime | None = None
        self.data = {}

    # ------------------------------------------------------------------ Setup

    @property
    def cloud_raw_sample(self) -> dict[str, Any] | None:
        """Neuester Roh-Datensatz aus der NIU-Cloud (Diagnose)."""
        return self._cloud.last_raw if self._cloud else None

    @property
    def cloud_enabled(self) -> bool:
        """Sind NIU-Zugangsdaten hinterlegt?"""
        return self._cloud is not None

    async def async_load(self) -> None:
        """Gespeicherte Werte laden und Callbacks registrieren."""
        if stored := await self._store.async_load():
            self.data = stored.get("values", {})
            self._serial = stored.get("serial")
            if stored.get("rides_version") == RIDES_VERSION:
                self._rides = stored.get("rides", {})
            self._refresh_ride_list()
            if ts := stored.get("last_success"):
                self.last_success = dt_util.parse_datetime(ts)
            if ts := stored.get("cloud_last_sync"):
                self.cloud_last_sync = dt_util.parse_datetime(ts)

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
        if self._cloud:
            entry.async_on_unload(
                async_track_time_interval(self.hass, self._async_cloud_tick, CLOUD_INTERVAL)
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

    def _refresh_ride_list(self) -> None:
        self.ride_list = R.sorted_rides(
            {key: ride for key, ride in self._rides.items() if not ride.get("skip")}
        )

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
            "serial": self._serial,
            "rides": self._rides,
            "rides_version": RIDES_VERSION,
            "cloud_last_sync": self.cloud_last_sync.isoformat() if self.cloud_last_sync else None,
        }

    # ---------------------------------------------------------------- Bluetooth

    @callback
    def _async_on_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        self.last_seen = dt_util.utcnow()
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
        was_unsynced = self.rides_unsynced
        current.update(values)
        self.data = current
        self.last_success = dt_util.utcnow()

        if was_unsynced and self.rides_unsynced is False and self._cloud:
            # Die App hat gerade synchronisiert: Fahrten kurz danach aus der Cloud holen.
            self.config_entry.async_on_unload(
                async_call_later(self.hass, CLOUD_DELAY_AFTER_SYNC, self._async_cloud_tick)
            )

        await self._store.async_save(self._payload())
        return current

    # -------------------------------------------------------------------- Cloud

    async def _async_cloud_tick(self, _now: datetime | None = None) -> None:
        await self.async_sync_rides()

    async def async_sync_rides(self) -> int:
        """Neue Fahrten aus der NIU-Cloud holen. Liefert die Anzahl neuer Einträge."""
        if self._cloud is None:
            return 0
        async with self._cloud_lock:
            try:
                if self._serial is None:
                    self._serial = await self._cloud.find_serial(self.address)
                    if self._serial is None:
                        raise CloudError("Roller im NIU-Konto nicht gefunden")
                raw = await self._cloud.new_rides(self._serial, set(self._rides))
            except CloudError as err:
                self.cloud_error = str(err)
                _LOGGER.warning("NIU-Cloud-Abruf fehlgeschlagen: %s", err)
                self.async_update_listeners()
                return 0

            added = 0
            for item in raw:
                ride = R.normalize(item, self.capacity_wh)
                # Auch verworfene Datensätze merken, damit sie nicht jedes Mal neu geladen werden.
                self._rides[str(item.get("trackId"))] = ride or {"skip": True}
                added += ride is not None

            now = dt_util.utcnow()
            limit = (now - RETRY_TEMPERATURE).isoformat()
            fresh_ids = {str(item.get("trackId")) for item in raw}
            pending = [
                ride
                for key, ride in self._rides.items()
                if not ride.get("skip")
                and ride.get("temperatur") is None
                and (key in fresh_ids or ride["start"] > limit)
            ]
            await self._async_fill_temperatures(pending, now)

            self._refresh_ride_list()
            self.cloud_last_sync = now
            self.cloud_error = None
            await self._store.async_save(self._payload())
            self.async_update_listeners()
            if added:
                _LOGGER.debug("%s neue Fahrten aus der NIU-Cloud", added)
            return added

    async def _async_fill_temperatures(self, pending: list[dict[str, Any]], now: datetime) -> None:
        """Außentemperatur je Fahrt aus der Langzeitstatistik (Stundenmittel) ergänzen."""
        entity = self._temperature_entity
        if not entity or not pending:
            return
        starts = [datetime.fromisoformat(ride["start"]) for ride in pending]
        by_hour: dict[int, float] = {}
        if "recorder" in self.hass.config.components:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                statistics_during_period,
            )

            begin = min(starts).replace(minute=0, second=0, microsecond=0)
            end = max(starts) + timedelta(hours=1)
            try:
                stats = await get_instance(self.hass).async_add_executor_job(
                    partial(
                        statistics_during_period,
                        self.hass,
                        begin,
                        end,
                        {entity},
                        "hour",
                        None,
                        {"mean"},
                    )
                )
            except Exception as err:  # noqa: BLE001 - Statistik ist optional
                _LOGGER.debug("Temperaturstatistik nicht lesbar: %s", err)
                stats = {}
            for row in stats.get(entity, []):
                if row.get("mean") is not None:
                    by_hour[int(row["start"]) // 3600] = row["mean"]

        current = self._float_state(entity)
        for ride, start in zip(pending, starts, strict=True):
            mean = by_hour.get(int(start.timestamp()) // 3600)
            if mean is None and now - start < RECENT_RIDE:
                mean = current
            if mean is not None:
                ride["temperatur"] = round(mean, 1)
