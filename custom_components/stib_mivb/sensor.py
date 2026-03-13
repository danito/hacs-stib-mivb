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
    ATTR_POINT_IDS,
    ATTR_STOP_NAME_FR,
    ATTR_STOP_NAME_NL,
    CONF_LANGUAGE,
    CONF_STOP_GROUPS,
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
    groups = entry.data.get(CONF_STOP_GROUPS, [])

    entities: list[StibMivbSensor] = []

    for group in groups:
        # Create one sensor per passage (line+destination) found in the first
        # coordinator data fetch.  New lines discovered on refresh are added
        # dynamically via coordinator callbacks.
        passages = coordinator.data.get(group["name_fr"], [])
        for passage in passages:
            entities.append(StibMivbSensor(coordinator, group, passage, language))

    async_add_entities(entities, update_before_add=False)


class StibMivbSensor(CoordinatorEntity[StibMivbCoordinator], SensorEntity):
    """
    Sensor representing the waiting time for one line at a named stop group.

    Device  = stop name  (e.g. "FOREST NATIONAL")
    Sensor  = line + destination  (e.g. "Line 54 → FOREST (BERVOETS)")
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:bus-clock"

    def __init__(
        self,
        coordinator: StibMivbCoordinator,
        group: dict,
        passage: dict,
        language: str,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._language = language
        self._group = group

        self._name_fr: str = group["name_fr"]
        self._name_nl: str = group["name_nl"]
        self._point_ids: list[str] = group.get("point_ids", [])
        self._latitude = group.get("latitude")
        self._longitude = group.get("longitude")

        self._line_id: str = passage["line_id"]
        self._destination_fr: str = passage["destination_fr"]
        self._destination_nl: str = passage["destination_nl"]

        # Display name: use preferred language
        stop_display = self._name_fr if language == LANGUAGE_FRENCH else self._name_nl
        dest_display = self._destination_fr if language == LANGUAGE_FRENCH else self._destination_nl

        # Unique ID: stable across restarts
        # Use first point_id + line + destination_fr slug
        dest_slug = self._destination_fr.lower().replace(" ", "_").replace("(", "").replace(")", "")
        self._attr_unique_id = (
            f"{DOMAIN}_{self._point_ids[0]}_{self._line_id}_{dest_slug}"
        )

        # Sensor name: "Line 54 → FOREST (BERVOETS)"  under device "FOREST NATIONAL"
        self._attr_name = f"Line {self._line_id} → {dest_display}"

        # Device: one per stop group name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"stop_group_{self._name_fr}")},
            name=stop_display,
            manufacturer="STIB/MIVB",
            model=f"Stop group – {', '.join(self._point_ids)}",
        )

    @property
    def _current_passage(self) -> dict:
        """Find this sensor's passage in the latest coordinator data."""
        passages = self.coordinator.data.get(self._name_fr, [])
        for p in passages:
            if p["line_id"] == self._line_id and p["destination_fr"] == self._destination_fr:
                return p
        return {}

    @property
    def native_value(self) -> int | None:
        """Return minutes until next arrival."""
        return self._current_passage.get("minutes")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich attributes."""
        p = self._current_passage
        dest = (
            self._destination_fr if self._language == LANGUAGE_FRENCH
            else self._destination_nl
        )
        return {
            ATTR_NEXT_PASSAGE: p.get("next_passage"),
            ATTR_LATITUDE: self._latitude,
            ATTR_LONGITUDE: self._longitude,
            ATTR_STOP_NAME_FR: self._name_fr,
            ATTR_STOP_NAME_NL: self._name_nl,
            ATTR_DESTINATION: dest,
            ATTR_LINE_ID: self._line_id,
            ATTR_POINT_IDS: self._point_ids,
        }

    @property
    def available(self) -> bool:
        """Sensor is available when coordinator last update succeeded."""
        return self.coordinator.last_update_success
