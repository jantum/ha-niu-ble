"""Standort des Rollers: zuhause per Bluetooth, unterwegs über ein Handy."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.const import ATTR_GPS_ACCURACY, ATTR_LATITUDE, ATTR_LONGITUDE, STATE_NOT_HOME
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_TRACKER
from .coordinator import NiuConfigEntry, NiuCoordinator
from .entity import NiuEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NiuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Tracker anlegen."""
    async_add_entities([NiuTracker(entry.runtime_data, entry.options.get(CONF_TRACKER))])


class NiuTracker(NiuEntity, TrackerEntity):
    """In Bluetooth-Reichweite = zuhause, sonst Position des gewählten Handys."""

    _attr_name = None

    def __init__(self, coordinator: NiuCoordinator, phone_tracker: str | None) -> None:
        super().__init__(coordinator, "location")
        self._phone_tracker = phone_tracker

    async def async_added_to_hass(self) -> None:
        """Auf Positionsänderungen des Handys hören."""
        await super().async_added_to_hass()
        if self._phone_tracker:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._phone_tracker], self._async_phone_changed
                )
            )

    @callback
    def _async_phone_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self.coordinator.in_range:
            self.async_write_ha_state()

    def _phone_attr(self, attribute: str) -> Any:
        if not self._phone_tracker:
            return None
        state = self.hass.states.get(self._phone_tracker)
        return None if state is None else state.attributes.get(attribute)

    @property
    def source_type(self) -> SourceType:
        """Quelle der Position."""
        return SourceType.BLUETOOTH_LE if self.coordinator.in_range else SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Breitengrad."""
        if self.coordinator.in_range:
            return self.hass.config.latitude
        return self._phone_attr(ATTR_LATITUDE)

    @property
    def longitude(self) -> float | None:
        """Längengrad."""
        if self.coordinator.in_range:
            return self.hass.config.longitude
        return self._phone_attr(ATTR_LONGITUDE)

    @property
    def location_accuracy(self) -> float:
        """Genauigkeit in Metern."""
        if self.coordinator.in_range:
            return 0
        return self._phone_attr(ATTR_GPS_ACCURACY) or 0

    @property
    def location_name(self) -> str | None:
        """Ohne Handy-Position gilt der Roller unterwegs als 'nicht zuhause'."""
        if self.coordinator.in_range or self.latitude is not None:
            return None
        return STATE_NOT_HOME
