"""STIB/MIVB integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import StibMivbApiClient
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_STOP_GROUPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up STIB/MIVB from a config entry."""
    session = async_get_clientsession(hass)
    api_key = entry.data.get(CONF_API_KEY, "")
    client = StibMivbApiClient(session, api_key)

    # Verify connectivity
    try:
        details = await client.get_stop_details("2935")
        if not details:
            raise ConfigEntryNotReady("API key validation returned no data")
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(f"Cannot connect to STIB/MIVB API: {err}") from err

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    coordinator = StibMivbCoordinator(hass, client, entry, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


class StibMivbCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches waiting times for all configured stop groups."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: StibMivbApiClient,
        entry: ConfigEntry,
        scan_interval: int,
    ) -> None:
        """Initialise coordinator."""
        self.client = client
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        """
        Fetch waiting times for every configured stop group.

        Returns:
          {
            stop_group_name_fr: [
              {
                "line_id": str,
                "destination_fr": str,
                "destination_nl": str,
                "minutes": int | None,
                "next_passage": str | None,
                "point_id": str,
              },
              ...
            ],
            ...
          }
        """
        groups = self.entry.data.get(CONF_STOP_GROUPS, [])
        data: dict = {}

        for group in groups:
            name_fr = group["name_fr"]
            point_ids = group.get("point_ids", [])
            try:
                passages = await self.client.get_waiting_times_for_group(point_ids)
                data[name_fr] = passages
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed to update stop group %s: %s", name_fr, err)
                data[name_fr] = []

        return data
