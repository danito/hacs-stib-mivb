"""STIB/MIVB API client."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_STOP_DETAILS,
    API_STOPS_BY_LINE,
    API_WAITING_TIMES,
)

_LOGGER = logging.getLogger(__name__)


def _maybe_parse_json(value: Any) -> Any:
    """Parse a value that might be a JSON string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


class StibMivbApiClient:
    """API client for STIB/MIVB open data."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client."""
        self._session = session

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """Make a GET request and return the JSON response."""
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching %s: %s", url, err)
            raise

    async def get_stops_for_line(self, line_id: str) -> list[dict]:
        """
        Return a list of stop dicts for a given line, one entry per stop+direction
        combination.  The same physical stop will appear twice if it is served in
        both City and Suburb directions.

        Each dict: { id, name_fr, name_nl, latitude, longitude,
                     direction, destination_fr, destination_nl }
        """
        data = await self._get(API_STOPS_BY_LINE, params={"where": f"lineid={line_id}"})
        results = data.get("results", [])

        # Collect (stop_id, direction, dest_fr, dest_nl) – one tuple per occurrence.
        # Do NOT deduplicate here: the same stop_id in two directions needs two entries.
        raw_stops: list[tuple[str, str, str, str]] = []
        all_stop_ids: set[str] = set()

        for direction_row in results:
            direction = direction_row.get("direction", "")
            destination = _maybe_parse_json(direction_row.get("destination", {}))
            dest_fr = destination.get("fr", "") if isinstance(destination, dict) else str(destination)
            dest_nl = destination.get("nl", dest_fr) if isinstance(destination, dict) else str(destination)

            points = _maybe_parse_json(direction_row.get("points", []))
            if not isinstance(points, list):
                continue

            for point in points:
                stop_id = str(point.get("id", ""))
                if not stop_id:
                    continue
                raw_stops.append((stop_id, direction, dest_fr, dest_nl))
                all_stop_ids.add(stop_id)

        if not all_stop_ids:
            return []

        # Batch-fetch names + coordinates for all unique stop IDs in one API call.
        details_map = await self._get_stop_details_batch(all_stop_ids)

        stops: list[dict] = []
        for stop_id, direction, dest_fr, dest_nl in raw_stops:
            details = details_map.get(stop_id, {})
            stops.append(
                {
                    "id": stop_id,
                    "name_fr": details.get("name_fr", stop_id),
                    "name_nl": details.get("name_nl", stop_id),
                    "latitude": details.get("latitude"),
                    "longitude": details.get("longitude"),
                    "direction": direction,
                    "destination_fr": dest_fr,
                    "destination_nl": dest_nl,
                }
            )

        return stops

    async def _get_stop_details_batch(self, stop_ids: set[str]) -> dict[str, dict]:
        """
        Fetch name + coordinates for multiple stops in a single API call.
        Returns a dict keyed by stop_id.
        """
        # ODS WHERE syntax: id in ("2935","2936",...)
        id_list = ",".join(f'"{sid}"' for sid in stop_ids)
        where_clause = f"id in ({id_list})"
        _LOGGER.debug("Batch fetch – where=%r limit=%d", where_clause, len(stop_ids) + 10)
        try:
            data = await self._get(
                API_STOP_DETAILS,
                params={
                    "where": where_clause,
                    "limit": len(stop_ids) + 10,
                },
            )
            _LOGGER.debug("Batch fetch – total_count=%s results_returned=%d", data.get("total_count"), len(data.get("results", [])))
            results = data.get("results", [])
            if results:
                _LOGGER.debug("Batch fetch – first raw result: %s", results[0])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Batch stop details fetch failed, falling back to empty: %s", err)
            return {}

        details_map: dict[str, dict] = {}
        for row in results:
            sid = str(row.get("id", ""))
            if not sid:
                continue
            name = _maybe_parse_json(row.get("name", {}))
            coords = _maybe_parse_json(row.get("gpscoordinates", {}))
            name_fr = name.get("fr", sid) if isinstance(name, dict) else str(name)
            name_nl = name.get("nl", name_fr) if isinstance(name, dict) else str(name)
            lat = coords.get("latitude") if isinstance(coords, dict) else None
            lon = coords.get("longitude") if isinstance(coords, dict) else None
            details_map[sid] = {
                "name_fr": name_fr,
                "name_nl": name_nl,
                "latitude": lat,
                "longitude": lon,
            }

        return details_map

    async def get_stop_details(self, stop_id: str) -> dict:
        """Return name (fr/nl) and GPS coordinates for a single stop."""
        try:
            data = await self._get(API_STOP_DETAILS, params={"where": f"id={stop_id}"})
            results = data.get("results", [])
            if not results:
                return {}

            row = results[0]
            name = _maybe_parse_json(row.get("name", {}))
            coords = _maybe_parse_json(row.get("gpscoordinates", {}))

            name_fr = name.get("fr", stop_id) if isinstance(name, dict) else str(name)
            name_nl = name.get("nl", name_fr) if isinstance(name, dict) else str(name)
            lat = coords.get("latitude") if isinstance(coords, dict) else None
            lon = coords.get("longitude") if isinstance(coords, dict) else None

            return {
                "name_fr": name_fr,
                "name_nl": name_nl,
                "latitude": lat,
                "longitude": lon,
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch details for stop %s: %s", stop_id, err)
            return {}

    async def get_waiting_times(self, stop_id: str, line_id: str) -> dict:
        """
        Return waiting time info for a specific stop+line combination.

        Returns:
          {
            "minutes": int | None,
            "next_passage": str | None,  # ISO timestamp of second upcoming vehicle
            "destination_fr": str,
            "destination_nl": str,
          }
        """
        try:
            data = await self._get(API_WAITING_TIMES, params={"where": f"pointid={stop_id}"})
            results = data.get("results", [])

            for row in results:
                if str(row.get("lineid", "")) != str(line_id):
                    continue

                passing_times = _maybe_parse_json(row.get("passingtimes", []))
                if not isinstance(passing_times, list) or not passing_times:
                    return self._empty_waiting()

                first = passing_times[0]
                expected = first.get("expectedArrivalTime")
                destination = first.get("destination", {})

                dest_fr = destination.get("fr", "") if isinstance(destination, dict) else str(destination)
                dest_nl = destination.get("nl", dest_fr) if isinstance(destination, dict) else str(destination)

                minutes = self._minutes_until(expected)

                # Second passage (if available)
                next_passage = None
                if len(passing_times) > 1:
                    next_passage = passing_times[1].get("expectedArrivalTime")

                return {
                    "minutes": minutes,
                    "next_passage": next_passage,
                    "destination_fr": dest_fr,
                    "destination_nl": dest_nl,
                }

            return self._empty_waiting()

        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch waiting times for stop %s line %s: %s", stop_id, line_id, err)
            return self._empty_waiting()

    @staticmethod
    def _empty_waiting() -> dict:
        return {
            "minutes": None,
            "next_passage": None,
            "destination_fr": "",
            "destination_nl": "",
        }

    @staticmethod
    def _minutes_until(iso_timestamp: str | None) -> int | None:
        """Return whole minutes from now until the given ISO timestamp."""
        if not iso_timestamp:
            return None
        try:
            arrival = datetime.fromisoformat(iso_timestamp)
            now = datetime.now(tz=arrival.tzinfo)
            delta = (arrival - now).total_seconds()
            return max(0, int(delta // 60))
        except (ValueError, TypeError):
            return None"""STIB/MIVB API client."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_STOP_DETAILS,
    API_STOPS_BY_LINE,
    API_WAITING_TIMES,
)

_LOGGER = logging.getLogger(__name__)


def _maybe_parse_json(value: Any) -> Any:
    """Parse a value that might be a JSON string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


class StibMivbApiClient:
    """API client for STIB/MIVB open data."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client."""
        self._session = session

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """Make a GET request and return the JSON response."""
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching %s: %s", url, err)
            raise

    async def get_stops_for_line(self, line_id: str) -> list[dict]:
        """
        Return a list of stop dicts for a given line, one entry per stop+direction
        combination.  The same physical stop will appear twice if it is served in
        both City and Suburb directions.

        Each dict: { id, name_fr, name_nl, latitude, longitude,
                     direction, destination_fr, destination_nl }
        """
        data = await self._get(API_STOPS_BY_LINE, params={"where": f"lineid={line_id}"})
        results = data.get("results", [])

        # Collect (stop_id, direction, dest_fr, dest_nl) – one tuple per occurrence.
        # Do NOT deduplicate here: the same stop_id in two directions needs two entries.
        raw_stops: list[tuple[str, str, str, str]] = []
        all_stop_ids: set[str] = set()

        for direction_row in results:
            direction = direction_row.get("direction", "")
            destination = _maybe_parse_json(direction_row.get("destination", {}))
            dest_fr = destination.get("fr", "") if isinstance(destination, dict) else str(destination)
            dest_nl = destination.get("nl", dest_fr) if isinstance(destination, dict) else str(destination)

            points = _maybe_parse_json(direction_row.get("points", []))
            if not isinstance(points, list):
                continue

            for point in points:
                stop_id = str(point.get("id", ""))
                if not stop_id:
                    continue
                raw_stops.append((stop_id, direction, dest_fr, dest_nl))
                all_stop_ids.add(stop_id)

        if not all_stop_ids:
            return []

        # Batch-fetch names + coordinates for all unique stop IDs in one API call.
        details_map = await self._get_stop_details_batch(all_stop_ids)

        stops: list[dict] = []
        for stop_id, direction, dest_fr, dest_nl in raw_stops:
            details = details_map.get(stop_id, {})
            stops.append(
                {
                    "id": stop_id,
                    "name_fr": details.get("name_fr", stop_id),
                    "name_nl": details.get("name_nl", stop_id),
                    "latitude": details.get("latitude"),
                    "longitude": details.get("longitude"),
                    "direction": direction,
                    "destination_fr": dest_fr,
                    "destination_nl": dest_nl,
                }
            )

        return stops

    async def _get_stop_details_batch(self, stop_ids: set[str]) -> dict[str, dict]:
        """
        Fetch name + coordinates for multiple stops in a single API call.
        Returns a dict keyed by stop_id.
        """
        # ODS WHERE syntax: id in ("2935","2936",...)
        id_list = ",".join(f'"{sid}"' for sid in stop_ids)
        try:
            data = await self._get(
                API_STOP_DETAILS,
                params={
                    "where": f"id in ({id_list})",
                    "limit": len(stop_ids) + 10,
                },
            )
            results = data.get("results", [])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Batch stop details fetch failed, falling back to empty: %s", err)
            return {}

        details_map: dict[str, dict] = {}
        for row in results:
            sid = str(row.get("id", ""))
            if not sid:
                continue
            name = _maybe_parse_json(row.get("name", {}))
            coords = _maybe_parse_json(row.get("gpscoordinates", {}))
            name_fr = name.get("fr", sid) if isinstance(name, dict) else str(name)
            name_nl = name.get("nl", name_fr) if isinstance(name, dict) else str(name)
            lat = coords.get("latitude") if isinstance(coords, dict) else None
            lon = coords.get("longitude") if isinstance(coords, dict) else None
            details_map[sid] = {
                "name_fr": name_fr,
                "name_nl": name_nl,
                "latitude": lat,
                "longitude": lon,
            }

        return details_map

    async def get_stop_details(self, stop_id: str) -> dict:
        """Return name (fr/nl) and GPS coordinates for a single stop."""
        try:
            data = await self._get(API_STOP_DETAILS, params={"where": f"id={stop_id}"})
            results = data.get("results", [])
            if not results:
                return {}

            row = results[0]
            name = _maybe_parse_json(row.get("name", {}))
            coords = _maybe_parse_json(row.get("gpscoordinates", {}))

            name_fr = name.get("fr", stop_id) if isinstance(name, dict) else str(name)
            name_nl = name.get("nl", name_fr) if isinstance(name, dict) else str(name)
            lat = coords.get("latitude") if isinstance(coords, dict) else None
            lon = coords.get("longitude") if isinstance(coords, dict) else None

            return {
                "name_fr": name_fr,
                "name_nl": name_nl,
                "latitude": lat,
                "longitude": lon,
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch details for stop %s: %s", stop_id, err)
            return {}

    async def get_waiting_times(self, stop_id: str, line_id: str) -> dict:
        """
        Return waiting time info for a specific stop+line combination.

        Returns:
          {
            "minutes": int | None,
            "next_passage": str | None,  # ISO timestamp of second upcoming vehicle
            "destination_fr": str,
            "destination_nl": str,
          }
        """
        try:
            data = await self._get(API_WAITING_TIMES, params={"where": f"pointid={stop_id}"})
            results = data.get("results", [])

            for row in results:
                if str(row.get("lineid", "")) != str(line_id):
                    continue

                passing_times = _maybe_parse_json(row.get("passingtimes", []))
                if not isinstance(passing_times, list) or not passing_times:
                    return self._empty_waiting()

                first = passing_times[0]
                expected = first.get("expectedArrivalTime")
                destination = first.get("destination", {})

                dest_fr = destination.get("fr", "") if isinstance(destination, dict) else str(destination)
                dest_nl = destination.get("nl", dest_fr) if isinstance(destination, dict) else str(destination)

                minutes = self._minutes_until(expected)

                # Second passage (if available)
                next_passage = None
                if len(passing_times) > 1:
                    next_passage = passing_times[1].get("expectedArrivalTime")

                return {
                    "minutes": minutes,
                    "next_passage": next_passage,
                    "destination_fr": dest_fr,
                    "destination_nl": dest_nl,
                }

            return self._empty_waiting()

        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch waiting times for stop %s line %s: %s", stop_id, line_id, err)
            return self._empty_waiting()

    @staticmethod
    def _empty_waiting() -> dict:
        return {
            "minutes": None,
            "next_passage": None,
            "destination_fr": "",
            "destination_nl": "",
        }

    @staticmethod
    def _minutes_until(iso_timestamp: str | None) -> int | None:
        """Return whole minutes from now until the given ISO timestamp."""
        if not iso_timestamp:
            return None
        try:
            arrival = datetime.fromisoformat(iso_timestamp)
            now = datetime.now(tz=arrival.tzinfo)
            delta = (arrival - now).total_seconds()
            return max(0, int(delta // 60))
        except (ValueError, TypeError):
            return None
