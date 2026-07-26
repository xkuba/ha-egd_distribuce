"""EG.D Distribuce – DataUpdateCoordinator."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import EgdApi, EgdApiError
from .const import (
    CONF_EAN,
    CONF_HISTORY_FROM,
    CONF_METER_TYPE,
    CONF_UPDATE_HOUR,
    DEFAULT_SCAN_DAYS,
    DEFAULT_UPDATE_HOUR,
    DOMAIN,
    METER_TYPE_AB,
)

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
    "sharing_commercial_kwh": (
        "sharing_commercial",
        UnitOfEnergy.KILO_WATT_HOUR,
        "Sdílení energie – obchodní",
    ),
    "sharing_distribution_kwh": (
        "sharing_distribution",
        UnitOfEnergy.KILO_WATT_HOUR,
        "Sdílení energie – distribuční",
    ),
    "production_sharing_kwh": (
        "production_sharing",
        UnitOfEnergy.KILO_WATT_HOUR,
        "Dodávka ponížená v rámci sdílení",
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
        entry: ConfigEntry,
    ) -> None:
        self.api = api
        self.ean = ean
        self.entry_id = entry_id
        self._entry = entry
        self._initial_sync_done = False  # Příznak platný jen v rámci jednoho běhu HA
        self._latest_values: dict[str, float] = {}
        self._latest_dates: dict[str, date] = {}
        # Klíče dat, pro které API vrátilo alespoň jednu hodnotu.
        # None = zatím nezjištěno (synchronizace neproběhla) → senzory necháme povolené.
        self.available_data_keys: set[str] | None = None

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
        update_hour = self._entry.options.get(CONF_UPDATE_HOUR, DEFAULT_UPDATE_HOUR)

        if not self._initial_sync_done:
            # Nejdřív stav z recorderu – statistiky přežijí smazání integrace,
            # takže po znovupřidání se stahování přeskočí a jinak bychom zůstali
            # bez hodnot pro senzory i bez informace o dostupných profilech.
            await self._load_state_from_recorder()
            days = self._resolve_history_days()
            await self._sync_history(days)
            self._initial_sync_done = True
            return self._build_state()

        if now.hour >= update_hour:
            yesterday = date.today() - timedelta(days=1)
            # Stáhni jen pokud ještě nemáme včerejší data
            last_date = await self._get_last_recorded_date()
            if last_date is None or last_date < yesterday:
                await self._sync_range(yesterday, yesterday)

        return self._build_state()

    def _resolve_history_days(self) -> int:
        """Vrátí počet dní zpětné historie dle options (nebo výchozí hodnotu)."""
        history_from_str = self._entry.options.get(CONF_HISTORY_FROM, "").strip()
        if history_from_str:
            try:
                history_from = date.fromisoformat(history_from_str)
                days = (date.today() - history_from).days
                return max(days, 1)
            except ValueError:
                _LOGGER.warning("EGD: neplatné datum history_from '%s', použiji %d dní", history_from_str, DEFAULT_SCAN_DAYS)
        return DEFAULT_SCAN_DAYS

    @property
    def meter_type(self) -> str:
        """Typ měřiče z konfigurace."""
        return self._entry.data.get(CONF_METER_TYPE, METER_TYPE_AB)

    # ------------------------------------------------------------------
    # Synchronizace dat
    # ------------------------------------------------------------------

    async def _get_last_recorded_date(self) -> date | None:
        """Vrátí datum posledního záznamu spotřeby v recorderu (None = žádná data).

        Používá "sum" jako typ – start je vždy součástí výsledku bez ohledu na typ.
        """
        statistic_id = f"{DOMAIN}:{self.ean}_consumption"
        last_stats = await get_instance(self.hass).async_add_executor_job(
            lambda: get_last_statistics(self.hass, 1, statistic_id, True, {"sum"})
        )
        if last_stats and statistic_id in last_stats:
            start = last_stats[statistic_id][0].get("start")
            if start:
                return datetime.fromtimestamp(start, tz=dt_util.DEFAULT_TIME_ZONE).date()
        return None

    async def _load_state_from_recorder(self) -> None:
        """Načte poslední uložené hodnoty a dostupné profily z recorderu.

        Nevolá API. Slouží jako výchozí stav pro případ, kdy je historie už
        kompletní a stahování se přeskočí – bez toho by senzory zůstaly
        „neznámé" a nešlo by rozhodnout, které profily nemají data.
        """
        found: set[str] = set()

        for data_key, (stat_suffix, _unit, _name) in STAT_DEFINITIONS.items():
            statistic_id = f"{DOMAIN}:{self.ean}_{stat_suffix}"
            stats = await get_instance(self.hass).async_add_executor_job(
                lambda sid=statistic_id: get_last_statistics(
                    self.hass, 1, sid, True, {"state", "sum"}
                )
            )
            if not stats or statistic_id not in stats:
                continue

            row = stats[statistic_id][0]
            found.add(data_key)

            state = row.get("state")
            if state is not None:
                self._latest_values[data_key] = round(float(state), 4)

            start = row.get("start")
            if start:
                self._latest_dates[data_key] = datetime.fromtimestamp(
                    start, tz=dt_util.DEFAULT_TIME_ZONE
                ).date()

        # Dostupnost bereme jen když v recorderu je spotřeba – jinak je zřejmé,
        # že se zatím nic nestáhlo a nemáme co vyhodnocovat.
        if "consumption_kwh" in found:
            if self.available_data_keys is None:
                self.available_data_keys = found
            else:
                self.available_data_keys |= found
            _LOGGER.debug(
                "EGD: z recorderu načteny profily %s", sorted(found)
            )

    async def _sync_history(self, days: int) -> None:
        """Stáhne pouze chybějící historická data (nepřepisuje existující záznamy).

        Na základě posledního záznamu v recorderu určí od kdy data chybí.
        Díky tomu je bezpečné volat po každém restartu HA – stáhne jen nové dny.
        """
        today = date.today()
        target_from = today - timedelta(days=days)
        date_to = today - timedelta(days=1)

        last_date = await self._get_last_recorded_date()
        if last_date is None:
            date_from = target_from
        elif last_date >= date_to:
            _LOGGER.info("EGD: historická data jsou kompletní (poslední: %s), přeskakuji", last_date)
            return
        else:
            date_from = last_date + timedelta(days=1)

        _LOGGER.info(
            "EGD: synchronizuji historii %s – %s (%d dní, target od %s)",
            date_from,
            date_to,
            (date_to - date_from).days + 1,
            target_from,
        )
        await self._sync_range(date_from, date_to)

    async def _sync_range(self, date_from: date, date_to: date) -> None:
        """Stáhne a uloží data pro zadaný rozsah dat."""
        try:
            daily_data = await self.api.async_get_daily_data(
                self.ean, date_from, date_to, meter_type=self.meter_type
            )
        except EgdApiError as err:
            raise UpdateFailed(f"EGD API chyba: {err}") from err

        # Uložíme poslední dostupný den pro zobrazení v senzorech
        for data_key, daily in daily_data.items():
            if daily:
                last_day = max(daily.keys())
                self._latest_values[data_key] = round(daily[last_day], 4)
                self._latest_dates[data_key] = last_day

        # Zjištění dostupných profilů – jen pokud stahování evidentně fungovalo
        # (spotřeba má data). Sjednocujeme, aby profil, který jednou data vrátil,
        # nezmizel kvůli dni bez hodnot.
        if daily_data.get("consumption_kwh"):
            found = {key for key, daily in daily_data.items() if daily}
            if self.available_data_keys is None:
                self.available_data_keys = found
            else:
                self.available_data_keys |= found
            _LOGGER.debug("EGD: dostupné profily: %s", sorted(self.available_data_keys))

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
            mean_type=StatisticMeanType.NONE,
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

            # Timestamp = začátek dne v lokálním timezone (stejná zóna, pod kterou
            # api.py zařazuje čtvrthodiny do dnů – jinak by hodnoty spadly do jiného dne)
            dt_start = datetime(
                day.year, day.month, day.day, 0, 0, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE
            )

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
        """Vrátí dict s posledními dostupnými denními hodnotami pro senzory."""
        return {
            "ean": self.ean,
            "last_updated": dt_util.now().isoformat(),
            "values": self._latest_values,
            "dates": {k: v.isoformat() for k, v in self._latest_dates.items()},
        }

    # ------------------------------------------------------------------
    # Helper: statistic_id pro daný typ dat
    # ------------------------------------------------------------------

    def get_statistic_id(self, data_key: str) -> str:
        """Vrátí statistic_id pro daný klíč dat."""
        suffix = STAT_DEFINITIONS[data_key][0]
        return f"{DOMAIN}:{self.ean}_{suffix}"
