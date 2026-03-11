"""Config flow for STIB/MIVB integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import StibMivbApiClient
from .const import (
    CONF_LANGUAGE,
    CONF_LINE_ID,
    CONF_SCAN_INTERVAL,
    CONF_STOP_IDS,
    CONF_STOPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LANGUAGE_FRENCH,
    LANGUAGE_DUTCH,
)

_LOGGER = logging.getLogger(__name__)


class StibMivbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the STIB/MIVB config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._language: str = LANGUAGE_FRENCH
        # List of {"line_id": str, "stop_id": str, "stop_name_fr": str, "stop_name_nl": str, ...}
        self._configured_stops: list[dict] = []
        self._available_stops: list[dict] = []
        self._current_line_id: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 – choose display language."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}

        if user_input is not None:
            self._language = user_input[CONF_LANGUAGE]
            return await self.async_step_add_stop()

        schema = vol.Schema(
            {
                vol.Required(CONF_LANGUAGE, default=LANGUAGE_FRENCH): vol.In(
                    {LANGUAGE_FRENCH: "Français", LANGUAGE_DUTCH: "Nederlands"}
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_add_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 – pick a line then pick stops from that line."""
        errors: dict[str, str] = {}

        # Sub-step A: user typed a line number, fetch available stops
        if user_input is not None and CONF_LINE_ID in user_input and CONF_STOP_IDS not in user_input:
            line_id = str(user_input[CONF_LINE_ID]).strip()
            session = async_get_clientsession(self.hass)
            client = StibMivbApiClient(session)
            try:
                stops = await client.get_stops_for_line(line_id)
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
                stops = []

            if not stops:
                errors[CONF_LINE_ID] = "invalid_line"
            else:
                self._available_stops = stops
                self._current_line_id = line_id

        # Sub-step B: user selected stops from the fetched list
        if user_input is not None and CONF_STOP_IDS in user_input:
            selected_ids: list[str] = user_input[CONF_STOP_IDS]
            if not selected_ids:
                errors[CONF_STOP_IDS] = "no_stops_selected"
            else:
                # Store each selected stop
                for stop in self._available_stops:
                    if stop["id"] in selected_ids:
                        self._configured_stops.append(
                            {
                                "line_id": self._current_line_id,
                                "stop_id": stop["id"],
                                "stop_name_fr": stop["name_fr"],
                                "stop_name_nl": stop["name_nl"],
                                "latitude": stop.get("latitude"),
                                "longitude": stop.get("longitude"),
                                "direction": stop.get("direction", ""),
                                "destination_fr": stop.get("destination_fr", ""),
                                "destination_nl": stop.get("destination_nl", ""),
                            }
                        )
                self._available_stops = []
                self._current_line_id = ""
                return await self.async_step_confirm()

        # Build the form.
        # If we already have stops loaded for a line, show the stop selector.
        if self._available_stops:
            stop_options = {
                s["id"]: f"{s['name_fr']} / {s['name_nl']} ({s['id']})"
                for s in self._available_stops
            }
            schema = vol.Schema(
                {
                    vol.Required(CONF_STOP_IDS): cv.multi_select(stop_options),
                }
            )
            return self.async_show_form(
                step_id="add_stop",
                data_schema=schema,
                errors=errors,
                description_placeholders={"line_id": self._current_line_id},
            )

        # Otherwise show the line-number input
        schema = vol.Schema(
            {
                vol.Required(CONF_LINE_ID): str,
            }
        )
        return self.async_show_form(step_id="add_stop", data_schema=schema, errors=errors)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3 – review selections, add more stops or finish."""
        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input.get("action", "finish")
            if action == "add_more":
                return await self.async_step_add_stop()
            # Finish
            return self._create_entry()

        stops_summary = "\n".join(
            f"Line {s['line_id']} – {s['stop_name_fr']} / {s['stop_name_nl']} ({s['stop_id']})"
            for s in self._configured_stops
        )

        schema = vol.Schema(
            {
                vol.Required("action", default="finish"): vol.In(
                    {"finish": "Finish setup", "add_more": "Add more stops"}
                )
            }
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"stops_summary": stops_summary or "None"},
        )

    def _create_entry(self) -> config_entries.FlowResult:
        return self.async_create_entry(
            title="STIB/MIVB",
            data={
                CONF_LANGUAGE: self._language,
                CONF_STOPS: self._configured_stops,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> StibMivbOptionsFlow:
        """Return the options flow."""
        return StibMivbOptionsFlow(config_entry)


class StibMivbOptionsFlow(config_entries.OptionsFlow):
    """Handle options (add/remove stops, scan interval)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise."""
        self._config_entry = config_entry
        self._configured_stops: list[dict] = list(
            config_entry.data.get(CONF_STOPS, [])
        )
        self._available_stops: list[dict] = []
        self._current_line_id: str = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Options menu."""
        if user_input is not None:
            action = user_input.get("action", "finish")
            if action == "add_stop":
                return await self.async_step_add_stop()
            if action == "finish":
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_STOPS: self._configured_stops,
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    },
                )

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                    int, vol.Range(min=10, max=3600)
                ),
                vol.Required("action", default="finish"): vol.In(
                    {"finish": "Save & close", "add_stop": "Add more stops"}
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_add_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Add a stop via options."""
        errors: dict[str, str] = {}

        if user_input is not None and CONF_LINE_ID in user_input and CONF_STOP_IDS not in user_input:
            line_id = str(user_input[CONF_LINE_ID]).strip()
            session = async_get_clientsession(self.hass)
            client = StibMivbApiClient(session)
            try:
                stops = await client.get_stops_for_line(line_id)
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
                stops = []

            if not stops:
                errors[CONF_LINE_ID] = "invalid_line"
            else:
                self._available_stops = stops
                self._current_line_id = line_id

        if user_input is not None and CONF_STOP_IDS in user_input:
            selected_ids: list[str] = user_input[CONF_STOP_IDS]
            for stop in self._available_stops:
                if stop["id"] in selected_ids:
                    self._configured_stops.append(
                        {
                            "line_id": self._current_line_id,
                            "stop_id": stop["id"],
                            "stop_name_fr": stop["name_fr"],
                            "stop_name_nl": stop["name_nl"],
                            "latitude": stop.get("latitude"),
                            "longitude": stop.get("longitude"),
                            "direction": stop.get("direction", ""),
                            "destination_fr": stop.get("destination_fr", ""),
                            "destination_nl": stop.get("destination_nl", ""),
                        }
                    )
            self._available_stops = []
            return await self.async_step_init()

        if self._available_stops:
            stop_options = {
                s["id"]: f"{s['name_fr']} / {s['name_nl']} ({s['id']})"
                for s in self._available_stops
            }
            schema = vol.Schema({vol.Required(CONF_STOP_IDS): cv.multi_select(stop_options)})
            return self.async_show_form(step_id="add_stop", data_schema=schema, errors=errors)

        schema = vol.Schema({vol.Required(CONF_LINE_ID): str})
        return self.async_show_form(step_id="add_stop", data_schema=schema, errors=errors)
