"""EG.D Distribuce – Config Flow (GUI nastavení)."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EgdApi, EgdApiError, EgdAuthError, EgdPermissionError, EgdUnsupportedMeterError
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EAN,
    CONF_HISTORY_FROM,
    CONF_METER_TYPE,
    CONF_TEST_MODE,
    CONF_UPDATE_HOUR,
    DEFAULT_SCAN_DAYS,
    DEFAULT_UPDATE_HOUR,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
        vol.Required(CONF_EAN): str,
        vol.Required(CONF_TEST_MODE, default=False): bool,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Ověří přihlašovací údaje a zjistí typ měřiče z /om."""
    session = async_get_clientsession(hass)
    api = EgdApi(
        session=session,
        client_id=data[CONF_CLIENT_ID],
        client_secret=data[CONF_CLIENT_SECRET],
        test_mode=data.get(CONF_TEST_MODE, False),
    )
    meter_type = await api.async_validate_credentials(data[CONF_EAN])
    return {
        "title": f"EG.D Distribuce – {data[CONF_EAN]}",
        CONF_METER_TYPE: meter_type,
    }


class EgdDistribuceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow pro EG.D Distribuce."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Krok 1 – zadání přihlašovacích údajů."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                user_input[CONF_EAN], raise_on_progress=False
            )
            self._abort_if_unique_id_configured()

            try:
                info = await _validate_input(self.hass, user_input)
            except EgdAuthError:
                errors["base"] = "invalid_auth"
            except EgdUnsupportedMeterError:
                errors["base"] = "unsupported_meter"
            except EgdPermissionError:
                errors["base"] = "invalid_ean"
            except EgdApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("EGD: neočekávaná chyba při validaci")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"],
                    data={**user_input, CONF_METER_TYPE: info[CONF_METER_TYPE]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "portal_url": "portal.distribuce24.cz",
                "path": "Správa účtů → Vzdálený přístup – OPENAPI",
            },
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-autentizace pokud token/secret přestane fungovat."""
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EgdOptionsFlowHandler:
        """Vrátí options flow handler."""
        return EgdOptionsFlowHandler()


class EgdOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow – nastavení hodiny stahování a počátku historie."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Zobrazí formulář s nastavením."""
        errors: dict[str, str] = {}

        current_hour = self.config_entry.options.get(CONF_UPDATE_HOUR, DEFAULT_UPDATE_HOUR)
        default_history = (date.today() - timedelta(days=DEFAULT_SCAN_DAYS)).isoformat()
        current_history = self.config_entry.options.get(CONF_HISTORY_FROM, default_history)

        if user_input is not None:
            history_from_str = user_input.get(CONF_HISTORY_FROM, "").strip()
            if history_from_str:
                try:
                    date.fromisoformat(history_from_str)
                except ValueError:
                    errors[CONF_HISTORY_FROM] = "invalid_date"

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_HOUR,
                    default=current_hour,
                ): vol.All(int, vol.Range(min=0, max=23)),
                vol.Optional(
                    CONF_HISTORY_FROM,
                    description={"suggested_value": current_history},
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
