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
    statistics_during_period,
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
        """Vrátí datum posledního KOMPLETNÍHO dne spotřeby v recorderu.

        Statistiky jsou hodinové, takže poslední záznam může být uprostřed dne.
        Za kompletní považujeme den, jehož poslední hodina (23:xx lokálně) je
        uložená – jinak vrátíme den předchozí, aby se zbytek dne dostáhl.
        """
        statistic_id = f"{DOMAIN}:{self.ean}_consumption"
        last_stats = await get_instance(self.hass).async_add_executor_job(
            lambda: get_last_statistics(self.hass, 1, statistic_id, True, {"sum"})
        )
        if not last_stats or statistic_id not in last_stats:
            return None

        start = last_stats[statistic_id][0].get("start")
        if not start:
            return None

        last_hour = datetime.fromtimestamp(start, tz=dt_util.DEFAULT_TIME_ZONE)
        if last_hour.hour == 23:
            return last_hour.date()
        return last_hour.date() - timedelta(days=1)

    async def _load_state_from_recorder(self) -> None:
        """Načte poslední uložené hodnoty a dostupné profily z recorderu.

        Nevolá API. Slouží jako výchozí stav pro případ, kdy je historie už
        kompletní a stahování se přeskočí – bez toho by senzory zůstaly
        „neznámé" a nešlo by rozhodnout, které profily nemají data.
        """
        # Statistiky jsou hodinové – denní součet získáme agregací period="day".
        # Pro sum-statistiky vrací HA kumulativní součet ke konci dne, takže
        # spotřeba dne = rozdíl dvou po sobě jdoucích dnů.
        stat_ids = {
            f"{DOMAIN}:{self.ean}_{suffix}": data_key
            for data_key, (suffix, _unit, _name) in STAT_DEFINITIONS.items()
        }
        window_start = (dt_util.now() - timedelta(days=10)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        stats = await get_instance(self.hass).async_add_executor_job(
            lambda: statistics_during_period(
                self.hass, window_start, None, set(stat_ids), "day", None, {"sum"}
            )
        )

        found: set[str] = set()

        for statistic_id, rows in stats.items():
            data_key = stat_ids.get(statistic_id)
            if data_key is None or not rows:
                continue

            found.add(data_key)

            last_sum = rows[-1].get("sum")
            if last_sum is None:
                continue
            prev_sum = rows[-2].get("sum") if len(rows) >= 2 else None
            value = last_sum - prev_sum if prev_sum is not None else last_sum

            self._latest_values[data_key] = round(value, 4)
            start = rows[-1].get("start")
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

    @staticmethod
    def _group_by_local_day(hourly: dict[datetime, float]) -> dict[date, float]:
        """Sečte hodinové hodnoty do lokálních kalendářních dnů."""
        daily: dict[date, float] = {}
        for hour_start, value in hourly.items():
            day = hour_start.astimezone(dt_util.DEFAULT_TIME_ZONE).date()
            daily[day] = daily.get(day, 0.0) + value
        return daily

    async def _sync_range(self, date_from: date, date_to: date) -> None:
        """Stáhne a uloží data pro zadaný rozsah dat."""
        try:
            hourly_data = await self.api.async_get_hourly_data(
                self.ean, date_from, date_to, meter_type=self.meter_type
            )
        except EgdApiError as err:
            raise UpdateFailed(f"EGD API chyba: {err}") from err

        # Pro senzory potřebujeme denní součet posledního dostupného dne
        for data_key, hourly in hourly_data.items():
            if not hourly:
                continue
            daily = self._group_by_local_day(hourly)
            last_day = max(daily.keys())
            self._latest_values[data_key] = round(daily[last_day], 4)
            self._latest_dates[data_key] = last_day

        # Zjištění dostupných profilů – jen pokud stahování evidentně fungovalo
        # (spotřeba má data). Sjednocujeme, aby profil, který jednou data vrátil,
        # nezmizel kvůli dni bez hodnot.
        if hourly_data.get("consumption_kwh"):
            found = {key for key, hourly in hourly_data.items() if hourly}
            if self.available_data_keys is None:
                self.available_data_keys = found
            else:
                self.available_data_keys |= found
            _LOGGER.debug("EGD: dostupné profily: %s", sorted(self.available_data_keys))

        # Zapíšeme každý datový typ jako statistiku
        for data_key, (stat_suffix, unit, name) in STAT_DEFINITIONS.items():
            hourly = hourly_data.get(data_key, {})
            if not hourly:
                continue

            statistic_id = f"{DOMAIN}:{self.ean}_{stat_suffix}"
            await self._import_statistics(
                statistic_id=statistic_id,
                unit=unit,
                name=f"EGD {self.ean} {name}",
                hourly=hourly,
            )

    async def _import_statistics(
        self,
        statistic_id: str,
        unit: str,
        name: str,
        hourly: dict[datetime, float],
    ) -> None:
        """
        Zapíše hodinové hodnoty do HA recorder jako external statistics.

        Statistiky HA mají hodinovou granularitu, takže Energy Dashboard
        zobrazí skutečný průběh dne, ne jeden sloupec.

        Zapisujeme jen hodiny novější než poslední uložený záznam – kumulativní
        součet by se jinak při opakovaném stažení téhož období započítal dvakrát.
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

        # Poslední uložený záznam – navazujeme na jeho kumulativní součet
        last_stats = await get_instance(self.hass).async_add_executor_job(
            lambda: get_last_statistics(self.hass, 1, statistic_id, True, {"sum"})
        )
        last_sum = 0.0
        last_start: datetime | None = None
        if last_stats and statistic_id in last_stats:
            row = last_stats[statistic_id][0]
            last_sum = row.get("sum") or 0.0
            start_ts = row.get("start")
            if start_ts:
                last_start = datetime.fromtimestamp(start_ts, tz=dt_util.UTC)

        statistics: list[StatisticData] = []
        running_sum = last_sum
        skipped = 0

        for hour_start in sorted(hourly.keys()):
            if last_start is not None and hour_start <= last_start:
                skipped += 1
                continue

            value = round(hourly[hour_start], 4)
            running_sum += value
            statistics.append(
                StatisticData(
                    start=hour_start,
                    sum=round(running_sum, 4),
                    state=value,
                )
            )

        if statistics:
            async_add_external_statistics(self.hass, metadata, statistics)
            _LOGGER.debug(
                "EGD: zapsáno %d hodinových statistik pro %s (%d již uložených přeskočeno)",
                len(statistics), statistic_id, skipped,
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
