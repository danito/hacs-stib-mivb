"""STIB/MIVB API client."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import aiohttp

from .const import (
    API_KEY_HEADER,
    API_STOP_DETAILS,
    API_WAITING_TIMES,
    LANGUAGE_FRENCH,
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

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialise the client."""
        self._session = session
        self._headers = {API_KEY_HEADER: api_key}
        # Full stop catalogue: { stop_id: {name_fr, name_nl, latitude, longitude} }
        self._stop_cache: dict[str, dict] = {}

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """Make a GET request and return the JSON response."""
        try:
            async with self._session.get(
                url,
                params=params,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching %s: %s", url, err)
            raise

    # ── Catalogue ────────────────────────────────────────────────────────────

    async def load_catalogue(self) -> None:
        """
        Download the full stop catalogue (~2445 stops) via pagination and
        store it in self._stop_cache.  Safe to call multiple times — a
        populated cache is never re-fetched.
        """
        if self._stop_cache:
            return

        _LOGGER.debug("Downloading full stop catalogue…")
        PAGE = 100
        offset = 0
        catalogue: dict[str, dict] = {}

        while True:
            try:
                data = await self._get(
                    API_STOP_DETAILS,
                    params={"limit": PAGE, "offset": offset},
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Catalogue fetch failed at offset %d: %s", offset, err
                )
                break

            results = data.get("results", [])
            total = data.get("total_count", 0)

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
                catalogue[sid] = {
                    "name_fr": name_fr,
                    "name_nl": name_nl,
                    "latitude": lat,
                    "longitude": lon,
                }

            offset += len(results)
            _LOGGER.debug("Catalogue: %d/%d stops loaded", offset, total)

            if not results or offset >= total:
                break

        _LOGGER.debug("Catalogue complete – %d stops loaded", len(catalogue))
        self._stop_cache = catalogue

    def search_stops(self, query: str, language: str = LANGUAGE_FRENCH) -> dict[str, dict]:
        """
        Search the cached catalogue for stops whose name contains `query`
        (case-insensitive).  Groups results by display name so that stops
        sharing a name (different physical platforms) are merged.

        Returns:
          {
            "FOREST NATIONAL": {
              "name_fr": "FOREST NATIONAL",
              "name_nl": "VORST NATIONAAL",
              "point_ids": ["2616B", "2732", "2953"],
              "latitude": 50.809...,   # from first matched point
              "longitude": 4.323...,
            },
            ...
          }
        """
        query_lower = query.strip().lower()
        grouped: dict[str, dict] = {}

        for sid, details in self._stop_cache.items():
            name_fr = details.get("name_fr", "")
            name_nl = details.get("name_nl", "")

            # Search in both languages
            if query_lower not in name_fr.lower() and query_lower not in name_nl.lower():
                continue

            # Group key is the display name in the chosen language
            group_key = name_fr if language == LANGUAGE_FRENCH else name_nl

            if group_key not in grouped:
                grouped[group_key] = {
                    "name_fr": name_fr,
                    "name_nl": name_nl,
                    "point_ids": [],
                    "latitude": details.get("latitude"),
                    "longitude": details.get("longitude"),
                }
            grouped[group_key]["point_ids"].append(sid)

        return dict(sorted(grouped.items()))

    # ── Waiting times ────────────────────────────────────────────────────────

    async def get_waiting_times_for_group(
        self, point_ids: list[str]
    ) -> list[dict]:
        """
        Fetch waiting times for all physical point IDs of a stop group and
        merge them into a deduplicated list of passages, one per line+direction.

        Returns a list of:
          {
            "line_id": str,
            "direction": str,
            "destination_fr": str,
            "destination_nl": str,
            "minutes": int | None,
            "next_passage": str | None,
            "point_id": str,   # which physical ID answered first
          }
        """
        import asyncio

        async def _fetch(pid: str) -> list[dict]:
            try:
                data = await self._get(
                    API_WAITING_TIMES, params={"where": f"pointid={pid}"}
                )
                return data.get("results", [])
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Waiting times fetch failed for %s: %s", pid, err)
                return []

        all_results = await asyncio.gather(*(_fetch(pid) for pid in point_ids))

        # Merge: key = (line_id, destination_fr) → keep earliest arrival
        merged: dict[tuple, dict] = {}

        for pid, results in zip(point_ids, all_results):
            for row in results:
                line_id = str(row.get("lineid", ""))
                passing_times = _maybe_parse_json(row.get("passingtimes", []))
                if not isinstance(passing_times, list) or not passing_times:
                    continue

                first = passing_times[0]
                destination = first.get("destination", {})
                dest_fr = destination.get("fr", "") if isinstance(destination, dict) else str(destination)
                dest_nl = destination.get("nl", dest_fr) if isinstance(destination, dict) else str(destination)
                expected = first.get("expectedArrivalTime")
                minutes = self._minutes_until(expected)
                next_passage = passing_times[1].get("expectedArrivalTime") if len(passing_times) > 1 else None

                key = (line_id, dest_fr)
                existing = merged.get(key)
                if existing is None or (minutes is not None and (existing["minutes"] is None or minutes < existing["minutes"])):
                    merged[key] = {
                        "line_id": line_id,
                        "destination_fr": dest_fr,
                        "destination_nl": dest_nl,
                        "minutes": minutes,
                        "next_passage": next_passage,
                        "point_id": pid,
                    }

        return list(merged.values())

    # ── Single stop detail (used for API key validation) ─────────────────────

    async def get_stop_details(self, stop_id: str) -> dict:
        """Return details for a single stop — used to validate the API key."""
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
            return {
                "name_fr": name_fr,
                "name_nl": name_nl,
                "latitude": coords.get("latitude") if isinstance(coords, dict) else None,
                "longitude": coords.get("longitude") if isinstance(coords, dict) else None,
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch details for stop %s: %s", stop_id, err)
            return {}

    # ── Helpers ───────────────────────────────────────────────────────────────

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
