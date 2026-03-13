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
    CONF_API_KEY,
    CONF_LANGUAGE,
    CONF_SCAN_INTERVAL,
    CONF_STOP_GROUPS,
    CONF_STOP_NAME,
    CONF_STOP_SEARCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LANGUAGE_DUTCH,
    LANGUAGE_FRENCH,
)

_LOGGER = logging.getLogger(__name__)


class StibMivbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the STIB/MIVB config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._language: str = LANGUAGE_FRENCH
        self._api_key: str = ""
        self._client: StibMivbApiClient | None = None
        self._configured_groups: list[dict] = []
        # Search state
        self._search_results: dict[str, dict] = {}  # display_name → group dict

    # ── Step 1: language + API key ────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose language and enter API key."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}

        if user_input is not None:
            self._language = user_input[CONF_LANGUAGE]
            self._api_key = user_input[CONF_API_KEY].strip()

            session = async_get_clientsession(self.hass)
            self._client = StibMivbApiClient(session, self._api_key)

            # Validate key with a quick test call
            try:
                details = await self._client.get_stop_details("2935")
                if not details:
                    errors[CONF_API_KEY] = "invalid_api_key"
                else:
                    # Key is valid — download the full catalogue
                    await self._client.load_catalogue()
                    return await self.async_step_search()
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_LANGUAGE, default=LANGUAGE_FRENCH): vol.In(
                    {LANGUAGE_FRENCH: "Français", LANGUAGE_DUTCH: "Nederlands"}
                ),
                vol.Required(CONF_API_KEY): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ── Step 2: search by name ────────────────────────────────────────────────

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Enter a search term to find stop names."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get(CONF_STOP_SEARCH, "").strip()
            if len(query) < 2:
                errors[CONF_STOP_SEARCH] = "search_too_short"
            else:
                self._search_results = self._client.search_stops(query, self._language)
                if not self._search_results:
                    errors[CONF_STOP_SEARCH] = "no_results"
                else:
                    return await self.async_step_pick_stop()

        schema = vol.Schema({vol.Required(CONF_STOP_SEARCH): str})
        return self.async_show_form(
            step_id="search",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "already_added": ", ".join(
                    g["name_fr"] for g in self._configured_groups
                ) or "none"
            },
        )

    # ── Step 3: pick one stop name from search results ────────────────────────

    async def async_step_pick_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Select a stop name from the search results."""
        errors: dict[str, str] = {}

        if user_input is not None:
            chosen_name = user_input[CONF_STOP_NAME]
            group = self._search_results.get(chosen_name)
            if group:
                # Avoid exact duplicates (same name_fr already added)
                already = {g["name_fr"] for g in self._configured_groups}
                if group["name_fr"] not in already:
                    self._configured_groups.append(group)
            self._search_results = {}
            return await self.async_step_confirm()

        # Build option list: display_name → "NAME (N platforms)"
        options = {
            name: f"{name}  ({len(g['point_ids'])} platform{'s' if len(g['point_ids']) > 1 else ''})"
            for name, g in self._search_results.items()
        }

        schema = vol.Schema(
            {vol.Required(CONF_STOP_NAME): vol.In(options)}
        )
        return self.async_show_form(
            step_id="pick_stop", data_schema=schema, errors=errors
        )

    # ── Step 4: confirm + add more or finish ─────────────────────────────────

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Show configured stops and offer to add more or finish."""
        if user_input is not None:
            action = user_input.get("action", "finish")
            if action == "add_more":
                return await self.async_step_search()
            return self._create_entry()

        stops_summary = "\n".join(
            f"• {g['name_fr']} / {g['name_nl']}  [{', '.join(g['point_ids'])}]"
            for g in self._configured_groups
        )

        schema = vol.Schema(
            {
                vol.Required("action", default="finish"): vol.In(
                    {"finish": "Finish setup", "add_more": "Add another stop"}
                )
            }
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=schema,
            description_placeholders={"stops_summary": stops_summary or "None"},
        )

    def _create_entry(self) -> config_entries.FlowResult:
        return self.async_create_entry(
            title="STIB/MIVB",
            data={
                CONF_API_KEY: self._api_key,
                CONF_LANGUAGE: self._language,
                CONF_STOP_GROUPS: self._configured_groups,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> StibMivbOptionsFlow:
        """Return the options flow."""
        return StibMivbOptionsFlow(config_entry)


class StibMivbOptionsFlow(config_entries.OptionsFlow):
    """Options: add/remove stop groups, scan interval."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialise."""
        self._config_entry = config_entry
        self._configured_groups: list[dict] = list(
            config_entry.data.get(CONF_STOP_GROUPS, [])
        )
        self._client: StibMivbApiClient | None = None
        self._search_results: dict[str, dict] = {}
        self._language: str = config_entry.data.get(CONF_LANGUAGE, LANGUAGE_FRENCH)

    async def _ensure_client(self) -> None:
        """Create and warm up the API client if not done yet."""
        if self._client is None:
            session = async_get_clientsession(self.hass)
            api_key = self._config_entry.data.get(CONF_API_KEY, "")
            self._client = StibMivbApiClient(session, api_key)
            await self._client.load_catalogue()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Options menu."""
        if user_input is not None:
            action = user_input.get("action", "finish")
            if action == "add_stop":
                await self._ensure_client()
                return await self.async_step_search()
            return self.async_create_entry(
                title="",
                data={
                    CONF_STOP_GROUPS: self._configured_groups,
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
                    {"finish": "Save & close", "add_stop": "Add another stop"}
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Search for a stop by name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get(CONF_STOP_SEARCH, "").strip()
            if len(query) < 2:
                errors[CONF_STOP_SEARCH] = "search_too_short"
            else:
                self._search_results = self._client.search_stops(query, self._language)
                if not self._search_results:
                    errors[CONF_STOP_SEARCH] = "no_results"
                else:
                    return await self.async_step_pick_stop()

        schema = vol.Schema({vol.Required(CONF_STOP_SEARCH): str})
        return self.async_show_form(step_id="search", data_schema=schema, errors=errors)

    async def async_step_pick_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Pick a stop from search results."""
        if user_input is not None:
            chosen_name = user_input[CONF_STOP_NAME]
            group = self._search_results.get(chosen_name)
            if group:
                already = {g["name_fr"] for g in self._configured_groups}
                if group["name_fr"] not in already:
                    self._configured_groups.append(group)
            self._search_results = {}
            return await self.async_step_init()

        options = {
            name: f"{name}  ({len(g['point_ids'])} platform{'s' if len(g['point_ids']) > 1 else ''})"
            for name, g in self._search_results.items()
        }
        schema = vol.Schema({vol.Required(CONF_STOP_NAME): vol.In(options)})
        return self.async_show_form(step_id="pick_stop", data_schema=schema)
