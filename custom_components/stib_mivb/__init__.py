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

    # Build the static line skeleton before the first refresh so that sensors
    # for ALL lines serving a stop are created immediately — even when no
    # vehicle is currently en route (which would make them invisible in rt data).
    await coordinator.async_build_static_lines()

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
        # Static skeleton: { name_fr: [ {line_id, dest_fr, dest_nl, direction} ] }
        # Built once at setup via async_build_static_lines().
        # This is what sensor.py uses to pre-create all sensors.
        self.static_lines: dict[str, list[dict]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def async_build_static_lines(self) -> None:
        """
        For each configured stop group, discover every line that serves any of
        its point IDs via the stopsByLine static dataset.  Populates
        self.static_lines so that sensor.py can create sensors for all lines
        upfront, regardless of whether a vehicle is currently en route.
        """
        groups = self.entry.data.get(CONF_STOP_GROUPS, [])
        for group in groups:
            name_fr = group["name_fr"]
            point_ids = group.get("point_ids", [])
            try:
                lines = await self.client.get_lines_for_points(point_ids)
                # Flatten to a list of passage skeletons
                skeletons: list[dict] = []
                for line_id, directions in lines.items():
                    for d in directions:
                        skeletons.append({
                            "line_id": line_id,
                            "dest_fr": d["dest_fr"],
                            "dest_nl": d["dest_nl"],
                            "direction": d["direction"],
                            "minutes": None,
                            "next_passage": None,
                            "rt_dest_fr": None,
                            "rt_dest_nl": None,
                            "point_id": None,
                        })
                self.static_lines[name_fr] = skeletons
                _LOGGER.debug(
                    "Static lines for %s: %s",
                    name_fr,
                    [(s["line_id"], s["dest_fr"]) for s in skeletons],
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Could not build static lines for %s: %s", name_fr, err
                )
                self.static_lines[name_fr] = []

    async def _async_update_data(self) -> dict:
        """
        Fetch real-time waiting times for every stop group and merge them on top
        of the static skeleton so that:
          - Every statically known line always has a sensor (minutes=None when
            no vehicle is currently en route).
          - Short-turn destinations from rt data are matched to the canonical
            (static) destination and do not create duplicate sensors.

        Coordinator data structure per stop group:
          [
            {
              "line_id":      str,
              "dest_fr":      str,   # canonical end-of-line destination (FR)
              "dest_nl":      str,   # canonical end-of-line destination (NL)
              "direction":    str,   # "City" | "Suburb"
              "rt_dest_fr":   str | None,  # real-time destination (may be short-turn)
              "rt_dest_nl":   str | None,
              "minutes":      int | None,
              "next_passage": str | None,
              "point_id":     str | None,
            },
            ...
          ]
        """
        groups = self.entry.data.get(CONF_STOP_GROUPS, [])
        data: dict = {}

        for group in groups:
            name_fr = group["name_fr"]
            point_ids = group.get("point_ids", [])

            # Start from the static skeleton (all lines, minutes=None)
            skeleton: dict[tuple, dict] = {}
            for s in self.static_lines.get(name_fr, []):
                key = (s["line_id"], s["dest_fr"])
                skeleton[key] = dict(s)  # copy so we don't mutate static_lines

            try:
                rt_passages = await self.client.get_waiting_times_for_group(point_ids)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Real-time fetch failed for %s: %s", name_fr, err)
                data[name_fr] = list(skeleton.values())
                continue

            # Merge real-time data into the skeleton.
            # Match rt passage to static entry by line_id; within that line,
            # try exact dest match first, then fall back to same direction.
            for p in rt_passages:
                line_id = p["line_id"]
                rt_dest_fr = p.get("rt_dest_fr", "")

                # Find the best matching static entry for this line
                # 1) Exact canonical dest match
                matched_key = next(
                    (k for k in skeleton if k[0] == line_id and k[1] == rt_dest_fr),
                    None,
                )
                # 2) Any entry for this line (short-turn case)
                if matched_key is None:
                    matched_key = next(
                        (k for k in skeleton if k[0] == line_id),
                        None,
                    )

                if matched_key is not None:
                    skeleton[matched_key].update({
                        "rt_dest_fr": rt_dest_fr,
                        "rt_dest_nl": p.get("rt_dest_nl"),
                        "minutes": p.get("minutes"),
                        "next_passage": p.get("next_passage"),
                        "point_id": p.get("point_id"),
                    })
                else:
                    # Line not in static skeleton (shouldn't normally happen);
                    # add it anyway with rt destination as canonical fallback.
                    _LOGGER.debug(
                        "Line %s at %s not in static skeleton, adding from rt",
                        line_id, name_fr,
                    )
                    skeleton[(line_id, rt_dest_fr)] = {
                        "line_id": line_id,
                        "dest_fr": rt_dest_fr,
                        "dest_nl": p.get("rt_dest_nl", ""),
                        "direction": "",
                        "rt_dest_fr": rt_dest_fr,
                        "rt_dest_nl": p.get("rt_dest_nl"),
                        "minutes": p.get("minutes"),
                        "next_passage": p.get("next_passage"),
                        "point_id": p.get("point_id"),
                    }

            data[name_fr] = list(skeleton.values())

        return data
