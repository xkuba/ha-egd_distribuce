"""EG.D Distribuce – DataUpdateCoordinator."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy, UnitOfReactivePower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import EgdApi, EgdApiError
from .const import CONF_EAN, DEFAULT_SCAN_DAYS, DEFAULT_UPDATE_HOUR, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Mapování: klíč dat → (statistic_id suffix, unit, popis)
STAT_DEFINITIONS = {
    "consumption_kwh": (
        "consumption",
        UnitOfEnergy.KILO_WATT_HOUR,
        "Spotřeba ze sítě",
    ),
    "production_kwh": (
        "production",
        UnitOfEnergy.KILO_WATT_HOUR,
        "Dodávka do sítě (FVE přetoky)",
    ),
    "reactive_consumption_kvarh": (
        "reactive_consumption",
        "kvarh",
        "Jalová spotřeba",
    ),
    "reactive_production_kvarh": (
        "reactive_production",
        "kvarh",
        "Jalová dodávka",
    ),
}


class EgdCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Koordinátor stahování a ukládání dat z EG.D API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EgdApi,
        ean: str,
        entry_id: str,
    ) -> None:
        self.api = api
        self.ean = ean
        self.entry_id = entry_id
        self._initial_sync_done = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"EGD Distribuce {ean}",
            # Aktualizace každou hodinu – skutečné stahování probíhá jen jednou denně
            update_interval=timedelta(hours=1),
        )

    # ------------------------------------------------------------------
    # Hlavní update metoda
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Stáhne nová data a zapíše statistiky do HA recorderu."""
        now = dt_util.now()

        # Při prvním spuštění stáhneme historii za posledních N dní
        if not self._initial_sync_done:
            await self._sync_history(DEFAULT_SCAN_DAYS)
            self._initial_sync_done = True
            return self._build_state()

        # Denní aktualizace: stahujeme jen pokud je >= DEFAULT_UPDATE_HOUR
        # (data jsou dostupná až odpoledne)
        if now.hour >= DEFAULT_UPDATE_HOUR:
            yesterday = date.today() - timedelta(days=1)
            await self._sync_range(yesterday, yesterday)

        return self._build_state()

    # ------------------------------------------------------------------
    # Synchronizace dat
    # ------------------------------------------------------------------

    async def _sync_history(self, days: int) -> None:
        """Stáhne historii za posledních `days` dní."""
        today = date.today()
        date_from = today - timedelta(days=days)
        date_to = today - timedelta(days=1)
        _LOGGER.info(
            "EGD: počáteční synchronizace %s – %s (%d dní)",
            date_from,
            date_to,
            days,
        )
        await self._sync_range(date_from, date_to)

    async def _sync_range(self, date_from: date, date_to: date) -> None:
        """Stáhne a uloží data pro zadaný rozsah dat."""
        try:
            daily_data = await self.api.async_get_daily_data(
                self.ean, date_from, date_to
            )
        except EgdApiError as err:
            raise UpdateFailed(f"EGD API chyba: {err}") from err

        # Zapíšeme každý datový typ jako statistiku
        for data_key, (stat_suffix, unit, name) in STAT_DEFINITIONS.items():
            daily = daily_data.get(data_key, {})
            if not daily:
                continue

            statistic_id = f"{DOMAIN}:{self.ean}_{stat_suffix}"
            await self._import_statistics(
                statistic_id=statistic_id,
                unit=unit,
                name=f"EGD {self.ean} {name}",
                daily=daily,
            )

    async def _import_statistics(
        self,
        statistic_id: str,
        unit: str,
        name: str,
        daily: dict[date, float],
    ) -> None:
        """
        Zapíše denní hodnoty do HA recorder jako external statistics.

        Každá hodnota je uložena na začátek příslušného dne (00:00 lokálního času).
        Tím zajistíme správné zobrazení v Energy Dashboard bez ohledu
        na to, kdy byla data stažena.
        """
        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=name,
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=unit,
        )

        # Získáme poslední uložený součet pro správné kumulativní počítání
        last_stats = await get_instance(self.hass).async_add_executor_job(
            lambda: get_last_statistics(self.hass, 1, statistic_id, True, {"sum"})
        )
        last_sum = 0.0
        if last_stats and statistic_id in last_stats:
            last_sum = last_stats[statistic_id][0].get("sum") or 0.0

        # Seřadíme dny chronologicky
        statistics: list[StatisticData] = []
        running_sum = last_sum

        for day in sorted(daily.keys()):
            value = round(daily[day], 4)
            running_sum += value

            # Timestamp = začátek dne v lokálním timezone
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            dt_start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=local_tz)

            statistics.append(
                StatisticData(
                    start=dt_start,
                    sum=round(running_sum, 4),
                    state=value,
                )
            )

        if statistics:
            async_add_external_statistics(self.hass, metadata, statistics)
            _LOGGER.debug(
                "EGD: zapsáno %d statistik pro %s", len(statistics), statistic_id
            )

    # ------------------------------------------------------------------
    # Stavový objekt pro senzory (aktuální hodnota = včerejšek)
    # ------------------------------------------------------------------

    def _build_state(self) -> dict[str, Any]:
        """Vrátí dict se včerejšími hodnotami pro klasické senzory."""
        # Senzory zobrazují poslední dostupný den (včera)
        # Historická data jsou v recorder statistikách
        return {
            "ean": self.ean,
            "last_updated": dt_util.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Helper: statistic_id pro daný typ dat
    # ------------------------------------------------------------------

    def get_statistic_id(self, data_key: str) -> str:
        """Vrátí statistic_id pro daný klíč dat."""
        suffix = STAT_DEFINITIONS[data_key][0]
        return f"{DOMAIN}:{self.ean}_{suffix}"
