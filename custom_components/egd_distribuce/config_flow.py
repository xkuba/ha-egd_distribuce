"""EG.D Distribuce – Config Flow (GUI nastavení)."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EgdApi, EgdApiError, EgdAuthError, EgdPermissionError
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_EAN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID, description={"suggested_value": ""}): str,
        vol.Required(CONF_CLIENT_SECRET, description={"suggested_value": ""}): str,
        vol.Required(CONF_EAN, description={"suggested_value": ""}): str,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Ověří přihlašovací údaje proti EG.D API."""
    session = async_get_clientsession(hass)
    api = EgdApi(
        session=session,
        client_id=data[CONF_CLIENT_ID],
        client_secret=data[CONF_CLIENT_SECRET],
    )
    await api.async_validate_credentials(data[CONF_EAN])
    return {"title": f"EG.D Distribuce – {data[CONF_EAN]}"}


class EgdDistribuceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow pro EG.D Distribuce."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Krok 1 – zadání přihlašovacích údajů."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Zabráníme duplicitní konfiguraci stejného EAN
            await self.async_set_unique_id(user_input[CONF_EAN])
            self._abort_if_unique_id_configured()

            try:
                info = await _validate_input(self.hass, user_input)
            except EgdAuthError:
                errors["base"] = "invalid_auth"
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
                    data=user_input,
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
