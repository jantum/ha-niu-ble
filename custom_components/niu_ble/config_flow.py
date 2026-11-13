"""Einrichtungsdialog der NIU-BLE-Integration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from bleak.exc import BleakError
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import async_read_scooter
from .const import (
    CONF_BLE_AES,
    CONF_BLE_PASSWORD,
    CONF_INTERVAL,
    CONF_ODOMETER,
    CONF_TEMPERATURE,
    CONF_TRACKER,
    DEFAULT_INTERVAL_MIN,
    DOMAIN,
    MAX_INTERVAL_MIN,
    MIN_INTERVAL_MIN,
    POLL_TIMEOUT,
)
from .protocol import NiuAuthError, NiuError

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

TRACKER_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="device_tracker")
)
ODOMETER_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
TEMPERATURE_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
)
INTERVAL_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_INTERVAL_MIN,
        max=MAX_INTERVAL_MIN,
        step=1,
        unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)


class NiuBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtung über Bluetooth-Erkennung oder manuell."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NiuBleOptionsFlow:
        """Optionsdialog."""
        return NiuBleOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Roller per Bluetooth entdeckt."""
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address.upper()
        self.context["title_placeholders"] = {
            "name": f"{discovery_info.name} ({discovery_info.address})"
        }
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """MAC-Adresse und BLE-Schlüssel abfragen."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            password = user_input[CONF_BLE_PASSWORD].strip()
            aes = user_input.get(CONF_BLE_AES, "").strip()

            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_mac"
            if len(password) != 16:
                errors[CONF_BLE_PASSWORD] = "invalid_password"
            if len(aes) not in (0, 16, 32):
                errors[CONF_BLE_AES] = "invalid_aes"

            if not errors:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                error = await self._async_test(address, password, aes)
                if error:
                    errors["base"] = error

            if not errors:
                options: dict[str, Any] = {CONF_INTERVAL: DEFAULT_INTERVAL_MIN}
                if tracker := user_input.get(CONF_TRACKER):
                    options[CONF_TRACKER] = tracker
                return self.async_create_entry(
                    title="NIU KQi",
                    data={
                        CONF_ADDRESS: address,
                        CONF_BLE_PASSWORD: password,
                        CONF_BLE_AES: aes,
                    },
                    options=options,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS, default=self._address or vol.UNDEFINED): str,
                vol.Required(CONF_BLE_PASSWORD): str,
                vol.Optional(CONF_BLE_AES): str,
                vol.Optional(CONF_TRACKER): TRACKER_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def _async_test(self, address: str, password: str, aes: str) -> str | None:
        """Schlüssel am Roller prüfen, sofern er gerade in Reichweite ist."""
        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            # Nicht in Reichweite: ohne Test übernehmen, der erste Abruf zeigt es.
            return None
        try:
            async with asyncio.timeout(POLL_TIMEOUT):
                values = await async_read_scooter(device, password, aes, [["bms_soc_rt"]])
        except NiuAuthError:
            return "invalid_auth"
        except (BleakError, NiuError, TimeoutError) as err:
            _LOGGER.debug("Verbindungstest fehlgeschlagen: %s", err)
            return "cannot_connect"
        if "bms_soc_rt" not in values:
            # Handshake ok, aber Daten nicht entschlüsselbar: AES-Schlüssel falsch.
            return "invalid_aes_key"
        return None


class NiuBleOptionsFlow(OptionsFlow):
    """Handy-Tracker, Kilometerzähler, Außentemperatur und Abrufintervall ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optionen bearbeiten."""
        if user_input is not None:
            options: dict[str, Any] = {
                CONF_INTERVAL: int(user_input.get(CONF_INTERVAL, DEFAULT_INTERVAL_MIN))
            }
            for key in (CONF_TRACKER, CONF_ODOMETER, CONF_TEMPERATURE):
                if value := user_input.get(key):
                    options[key] = value
            return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Optional(CONF_TRACKER): TRACKER_SELECTOR,
                vol.Optional(CONF_ODOMETER): ODOMETER_SELECTOR,
                vol.Optional(CONF_TEMPERATURE): TEMPERATURE_SELECTOR,
                vol.Required(CONF_INTERVAL, default=DEFAULT_INTERVAL_MIN): INTERVAL_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )
