"""EG.D Distribuce – custom integrace pro Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EgdApi
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_EAN, CONF_TEST_MODE, COORDINATOR_KEY, DOMAIN
from .coordinator import EgdCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Nastaví integraci z config entry."""
    session = async_get_clientsession(hass)

    api = EgdApi(
        session=session,
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        test_mode=entry.data.get(CONF_TEST_MODE, False),
    )

    coordinator = EgdCoordinator(
        hass=hass,
        api=api,
        ean=entry.data[CONF_EAN],
        entry_id=entry.entry_id,
        entry=entry,
        # Rozvrh HDO se stahuje z jiného (veřejného) endpointu než měřená data
        hdo_session=session,
    )

    # První refresh – stáhne historii a nastaví statistiky
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        COORDINATOR_KEY: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Registrace listener pro reload při změně konfigurace
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Odstraní integraci."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload při změně konfigurace."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
