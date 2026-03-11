"""Sensor platform for STIB/MIVB."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import StibMivbCoordinator
from .const import (
    ATTR_DESTINATION,
    ATTR_DIRECTION,
    ATTR_LATITUDE,
    ATTR_LINE_ID,
    ATTR_LONGITUDE,
    ATTR_NEXT_PASSAGE,
    ATTR_STOP_ID,
    ATTR_STOP_NAME_FR,
    ATTR_STOP_NAME_NL,
    CONF_LANGUAGE,
    CONF_STOPS,
    DOMAIN,
    LANGUAGE_FRENCH,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up STIB/MIVB sensors from a config entry."""
    coordinator: StibMivbCoordinator = hass.data[DOMAIN][entry.entry_id]
    language = entry.data.get(CONF_LANGUAGE, LANGUAGE_FRENCH)
    stops = entry.data.get(CONF_STOPS, [])

    entities = [
        StibMivbSensor(coordinator, stop, language)
        for stop in stops
    ]
    async_add_entities(entities, update_before_add=True)


class StibMivbSensor(CoordinatorEntity[StibMivbCoordinator], SensorEntity):
    """Sensor representing a line/stop waiting time."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:bus-clock"

    def __init__(
        self,
        coordinator: StibMivbCoordinator,
        stop: dict,
        language: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._stop = stop
        self._language = language

        self._line_id = stop["line_id"]
        self._stop_id = stop["stop_id"]
        self._stop_name_fr = stop.get("stop_name_fr", self._stop_id)
        self._stop_name_nl = stop.get("stop_name_nl", self._stop_id)
        self._latitude = stop.get("latitude")
        self._longitude = stop.get("longitude")
        self._direction = stop.get("direction", "")
        self._destination_fr = stop.get("destination_fr", "")
        self._destination_nl = stop.get("destination_nl", "")

        # Use the language-preferred stop name for display
        stop_display = (
            self._stop_name_fr if language == LANGUAGE_FRENCH else self._stop_name_nl
        )
        # Normalise direction to a safe lowercase slug for use in IDs/names
        direction_slug = self._direction.lower().replace(" ", "_") if self._direction else "unknown"

        # Unique ID: domain_line_stop_direction  (direction makes it collision-proof)
        self._attr_unique_id = f"{DOMAIN}_{self._line_id}_{self._stop_id}_{direction_slug}"

        # Sensor name: "Line 54 – JUPITER (City)"
        self._attr_name = f"Line {self._line_id} – {stop_display} ({self._direction or 'Unknown'})"

        # Device = one per physical stop name (groups all lines at same stop)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"stop_{self._stop_id}")},
            name=stop_display,
            manufacturer="STIB/MIVB",
            model=f"Stop {self._stop_id}",
        )

    @property
    def _data(self) -> dict:
        """Shortcut to this sensor's coordinator data."""
        return self.coordinator.data.get((self._line_id, self._stop_id, self._direction), {})

    @property
    def native_value(self) -> int | None:
        """Return minutes until next arrival."""
        return self._data.get("minutes")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        dest = (
            self._data.get("destination_fr", self._destination_fr)
            if self._language == LANGUAGE_FRENCH
            else self._data.get("destination_nl", self._destination_nl)
        )

        return {
            ATTR_NEXT_PASSAGE: self._data.get("next_passage"),
            ATTR_LATITUDE: self._latitude,
            ATTR_LONGITUDE: self._longitude,
            ATTR_STOP_NAME_FR: self._stop_name_fr,
            ATTR_STOP_NAME_NL: self._stop_name_nl,
            ATTR_DIRECTION: self._direction,
            ATTR_DESTINATION: dest,
            ATTR_LINE_ID: self._line_id,
            ATTR_STOP_ID: self._stop_id,
        }

    @property
    def available(self) -> bool:
        """Sensor is available when coordinator last update succeeded."""
        return self.coordinator.last_update_success
