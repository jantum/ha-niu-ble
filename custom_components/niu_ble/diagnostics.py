"""Diagnosedaten der NIU-BLE-Integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_BLE_AES, CONF_BLE_PASSWORD, CONF_NIU_ACCOUNT, CONF_NIU_PW_HASH
from .coordinator import NiuConfigEntry

TO_REDACT = {CONF_BLE_PASSWORD, CONF_BLE_AES, CONF_NIU_ACCOUNT, CONF_NIU_PW_HASH}
# Ortsbezogene Felder im Cloud-Datensatz unkenntlich machen (Name bleibt sichtbar).
LOCATION_HINTS = (
    "lat", "lng", "lon", "point", "addr", "location", "gps", "coord", "track_items", "thumb",
)


def _redact_location(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "**REDACTED**"
            if any(hint in key.lower() for hint in LOCATION_HINTS)
            else _redact_location(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_location(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NiuConfigEntry
) -> dict[str, Any]:
    """Diagnose für einen Eintrag."""
    coordinator = entry.runtime_data
    raw = coordinator.cloud_raw_sample
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "ble_values": coordinator.data,
        "in_range": coordinator.in_range,
        "rides": len(coordinator.ride_list),
        "cloud_error": coordinator.cloud_error,
        "cloud_raw_ride_keys": sorted(raw) if raw else None,
        "cloud_raw_ride": _redact_location(raw) if raw else None,
    }
