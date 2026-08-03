"""EG.D Distribuce – custom integrace pro Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EgdApi
from .const import (
    CONF_ADVANCE_PERIODS,
    CONF_BILLING_DATE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EAN,
    CONF_HDO_A,
    CONF_HDO_B,
    CONF_HDO_CODE,
    CONF_HDO_DP,
    CONF_HDO_MODE,
    CONF_HDO_PSC,
    CONF_HDO_VARIANT,
    CONF_PRICE_PERIODS,
    CONF_TARIFF_ENTITY,
    CONF_TEST_MODE,
    COORDINATOR_KEY,
    DOMAIN,
)
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

    coordinator.remember_options()

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


# Nastavení, jejichž změna si vyžádá znovunačtení integrace. Ostatní volby
# (ceny, vyúčtování, hodina stahování, historie) coordinator čte živě při
# každém použití, takže u nich reload jen zbytečně shodí všechny entity.
_RELOAD_ON_CHANGE = (
    CONF_HDO_MODE,
    CONF_HDO_CODE,
    CONF_HDO_PSC,
    CONF_HDO_A,
    CONF_HDO_B,
    CONF_HDO_DP,
    CONF_HDO_VARIANT,
)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reaguje na změnu nastavení – reload jen když je opravdu potřeba.

    Plný reload znamená, že všechny entity na chvíli zmizí. Vyhneme se mu
    všude, kde stačí přepočítat: přibyl-li nebo zmizel-li senzor, reload nutný
    je, u změny cen nebo data vyúčtování stačí obnovit data.
    """
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    coordinator: EgdCoordinator | None = data.get(COORDINATOR_KEY) if data else None

    if coordinator is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    options = entry.options
    previous = coordinator.tracked_options

    hdo_changed = any(
        previous.get(key) != options.get(key) for key in _RELOAD_ON_CHANGE
    )
    # Zapnutí/vypnutí cen nebo vyúčtování mění sadu entit – tam reload nutný je
    entities_changed = (
        bool(previous.get(CONF_PRICE_PERIODS)) != bool(options.get(CONF_PRICE_PERIODS))
        or bool(previous.get(CONF_BILLING_DATE)) != bool(options.get(CONF_BILLING_DATE))
        or bool(previous.get(CONF_TARIFF_ENTITY)) != bool(options.get(CONF_TARIFF_ENTITY))
        or bool(previous.get(CONF_ADVANCE_PERIODS)) != bool(options.get(CONF_ADVANCE_PERIODS))
    )

    coordinator.remember_options()

    if entities_changed:
        _LOGGER.debug("EGD: změnila se sada entit, načítám integraci znovu")
        await hass.config_entries.async_reload(entry.entry_id)
        return

    if hdo_changed:
        # Stačí zahodit rozvrh, při dalším refreshi se stáhne podle nového kódu
        _LOGGER.debug("EGD: změněno nastavení HDO, obnovím rozvrh")
        coordinator.invalidate_hdo()

    await coordinator.async_request_refresh()
