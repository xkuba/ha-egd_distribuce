"""EG.D Distribuce – DataUpdateCoordinator."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
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

from .api import EgdApi, EgdApiError, EgdPermissionError
from .const import (
    CONF_ADVANCE_PAYMENT,
    CONF_ADVANCE_PERIODS,
    CONF_BILLING_DATE,
    CONF_EAN,
    CONF_HDO_A,
    CONF_HDO_B,
    CONF_HDO_CODE,
    CONF_HDO_DP,
    CONF_HDO_MODE,
    CONF_HDO_PSC,
    CONF_HDO_REFRESH_DAYS,
    CONF_HDO_VARIANT,
    CONF_HISTORY_FROM,
    CONF_METER_TYPE,
    CONF_PRICE_PERIODS,
    CONF_TARIFF_ENTITY,
    CONF_UPDATE_HOUR,
    CURRENCY_CZK,
    DEFAULT_HDO_REFRESH_DAYS,
    DEFAULT_SCAN_DAYS,
    DEFAULT_UPDATE_HOUR,
    DOMAIN,
    HDO_MODE_CLASSIC,
    HDO_MODE_NONE,
    HDO_MODE_SMART,
    METER_TYPE_AB,
    STAT_SUFFIX_COST,
    STAT_SUFFIX_TOTAL_COST,
    TARIFF_CHECK_MARGIN_MINUTES,
    TARIFF_NT,
    TARIFF_STATES,
    TARIFF_VT,
)
from .hdo import HdoClient, HdoError, HdoSchedule
from .pricing import AdvancePeriod, AdvanceSchedule, PriceList

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
        hdo_session: Any = None,
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

        self._hdo_client = HdoClient(hdo_session)
        self._hdo: HdoSchedule | None = None
        # Den, ke kterému je rozvrh načtený – distributor může časy změnit,
        # tak ho po nastavené periodě stahujeme znovu.
        self._hdo_date: date | None = None
        # Od kdy trvá neshoda kalendáře s elektroměrem (None = shoda)
        self._tariff_mismatch_since: datetime | None = None
        # Poslední nerozpoznaná hodnota z entity tarifu – ať nelogujeme donekonečna
        self._unknown_tariff_state: str | None = None
        # Náklady za probíhající měsíc (bez stálé platby) a měsíc, k němuž patří
        self._month_energy_cost: float | None = None
        # Náklady za energii od posledního vyúčtování
        self._billing_energy_cost: float | None = None
        self._month_key: tuple[int, int] | None = None
        # Kopie options z posledního nastavení – slouží k rozhodnutí,
        # jestli změna vyžaduje reload, nebo stačí přepočet.
        self.tracked_options: dict[str, Any] = {}

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

            # Součty čteme z databáze JEŠTĚ PŘED synchronizací. Zápis statistik
            # jde do fronty recorderu a v databázi se objeví se zpožděním, takže
            # čtení hned po zápisu by vrátilo neúplná čísla. Nově zapsané hodiny
            # si místo toho přičte _sync_costs.
            await self._load_month_cost()
            await self._load_billing_cost()

            days = self._resolve_history_days()
            await self._sync_history(days)
            await self._async_hdo()  # ať senzor tarifu zná rozvrh hned po startu
            self._initial_sync_done = True
            return self._build_state()

        # Obnova rozvrhu HDO – uvnitř se stahuje jen po uplynutí periody
        await self._async_hdo()

        if now.hour >= update_hour:
            yesterday = date.today() - timedelta(days=1)
            # Stáhni jen pokud ještě nemáme včerejší data
            last_date = await self._get_last_recorded_date()
            if last_date is None or last_date < yesterday:
                # Součty aktualizuje _sync_costs z právě zapsaných hodin,
                # znovu číst z databáze by bylo příliš brzy.
                await self._sync_range(yesterday, yesterday)

        # Přelom měsíce – měsíční náklady je potřeba načíst znovu
        if self._month_key != (now.year, now.month):
            await self._load_month_cost()

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

    def remember_options(self) -> None:
        """Zapamatuje si aktuální options pro příští porovnání."""
        self.tracked_options = dict(self._entry.options)

    def invalidate_hdo(self) -> None:
        """Zahodí načtený rozvrh, aby se při dalším refreshi stáhl znovu."""
        self._hdo = None
        self._hdo_date = None

    # ------------------------------------------------------------------
    # Tarif a ceny
    # ------------------------------------------------------------------

    @property
    def price_list(self) -> PriceList:
        """Cenová období z konfigurace."""
        return PriceList.from_options(self._entry.options.get(CONF_PRICE_PERIODS))

    @property
    def cost_statistic_id(self) -> str:
        """Statistika ceny za energii – tahle patří do panelu energie."""
        return f"{DOMAIN}:{self.ean}_{STAT_SUFFIX_COST}"

    @property
    def total_cost_statistic_id(self) -> str:
        """Statistika celkových nákladů včetně stálé platby."""
        return f"{DOMAIN}:{self.ean}_{STAT_SUFFIX_TOTAL_COST}"

    @property
    def pricing_enabled(self) -> bool:
        """Má integrace dost údajů, aby mohla počítat náklady?"""
        return bool(self.price_list)

    async def _async_hdo(self) -> HdoSchedule | None:
        """Rozvrh HDO dle konfigurace; None u jednotarifu nebo při chybě.

        Sezónní přechody zvládne rozvrh z paměti (drží všechny sezóny naráz),
        obnova je kvůli tomu, že distributor může změnit samotné časy. Volá se
        při každém ticku coordinatoru, ale stahuje jen po uplynutí periody.
        """
        today = dt_util.now().date()
        refresh_days = max(
            1,
            int(
                self._entry.options.get(
                    CONF_HDO_REFRESH_DAYS, DEFAULT_HDO_REFRESH_DAYS
                )
            ),
        )
        if (
            self._hdo_date is not None
            and (today - self._hdo_date).days < refresh_days
        ):
            return self._hdo

        options = self._entry.options
        mode = options.get(CONF_HDO_MODE, HDO_MODE_NONE)

        if mode == HDO_MODE_NONE:
            self._hdo = None
            self._hdo_date = today
            return None

        try:
            if mode == HDO_MODE_SMART:
                records = await self._hdo_client.async_match_smart(
                    options.get(CONF_HDO_CODE, "")
                )
            elif mode == HDO_MODE_CLASSIC:
                records = await self._hdo_client.async_match_classic(
                    options.get(CONF_HDO_PSC, ""),
                    options.get(CONF_HDO_A, ""),
                    options.get(CONF_HDO_B, ""),
                    options.get(CONF_HDO_DP, ""),
                )
            else:
                self._hdo = None
                self._hdo_date = today
                return None

            schedule = HdoClient.schedule_for(records, options.get(CONF_HDO_VARIANT))
        except HdoError as err:
            if self._hdo is not None:
                # Starý rozvrh je pořád lepší než žádný – zkusíme to za hodinu.
                # _hdo_date záměrně nenastavujeme, aby se obnova opakovala.
                _LOGGER.warning(
                    "EGD: rozvrh HDO se nepodařilo obnovit (%s), používám dosavadní",
                    err,
                )
            else:
                _LOGGER.error("EGD: rozvrh HDO se nepodařilo načíst: %s", err)
            return self._hdo

        changed = self._hdo is not None and schedule.differs_from(self._hdo, today)
        self._hdo = schedule
        self._hdo_date = today

        if changed:
            _LOGGER.info("EGD: rozvrh HDO se změnil, používám nové časy")
        else:
            _LOGGER.debug("EGD: rozvrh HDO obnoven (režim %s)", mode)

        return self._hdo

    @property
    def tariff_entity_id(self) -> str | None:
        """Entita s tarifem z elektroměru, pokud je nastavená."""
        return self._entry.options.get(CONF_TARIFF_ENTITY) or None

    def meter_tariff(self) -> str | None:
        """Tarif přečtený přímo z elektroměru; None když entita není nastavená.

        Elektroměr je pro živý tarif spolehlivější než kalendář – hlásí stav,
        podle kterého se skutečně účtuje, ne předpověď.
        """
        entity_id = self.tariff_entity_id
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in (None, "", "unknown", "unavailable"):
            return None

        raw = str(state.state).strip()
        tariff = TARIFF_STATES.get(raw.lower())

        if tariff is None and raw != self._unknown_tariff_state:
            # Bez tohohle by se integrace jen tiše vrátila ke kalendáři
            # a uživatel by netušil, že entita hlásí něco neočekávaného.
            self._unknown_tariff_state = raw
            _LOGGER.warning(
                "EGD: entita %s hlásí tarif %r, kterému nerozumím – používám "
                "kalendář HDO. Očekávám VT/NT, T2/T3 nebo 2/3; hodnotu lze "
                "přemapovat filtrem v ESPHome.",
                entity_id, raw,
            )
        elif tariff is not None:
            self._unknown_tariff_state = None

        return tariff

    def calendar_tariff(self, hdo: HdoSchedule | None = None) -> str | None:
        """Tarif podle rozvrhu HDO; None při jednotarifu nebo neznámém rozvrhu."""
        schedule = hdo if hdo is not None else self._hdo
        if schedule is None:
            return None
        low = schedule.is_low_tariff(dt_util.now())
        if low is None:
            return None
        return TARIFF_NT if low else TARIFF_VT

    def current_tariff(self, hdo: HdoSchedule | None = None) -> str | None:
        """Aktuálně platný tarif: "NT", "VT", nebo None při jednotarifu.

        Přednost má hodnota z elektroměru, kalendář je záloha.
        """
        return self.meter_tariff() or self.calendar_tariff(hdo)

    @property
    def tariff_source(self) -> str | None:
        """Odkud pochází aktuální tarif – "meter" nebo "calendar"."""
        if self.meter_tariff() is not None:
            return "meter"
        if self.calendar_tariff() is not None:
            return "calendar"
        return None

    def _near_tariff_switch(self) -> bool:
        """Jsme blízko přepnutí dle kalendáře?

        Kolem hranice se měřič a kalendář legitimně liší o pár minut, takže
        tam nemá smysl hlásit neshodu.
        """
        if self._hdo is None:
            return False

        now = dt_util.now()
        margin = timedelta(minutes=TARIFF_CHECK_MARGIN_MINUTES)

        upcoming = self._hdo.next_change(now)
        if upcoming is not None and upcoming - now <= margin:
            return True
        previous = self._hdo.next_change(now - margin)
        return previous is not None and previous <= now

    def check_tariff_agreement(self) -> bool | None:
        """Souhlasí kalendář HDO s tarifem z elektroměru?

        None = nelze porovnat (chybí jeden ze zdrojů nebo jsme na hranici).
        Neshoda skoro jistě znamená špatně zadaný HDO kód nebo relé, což by
        jinak tiše znehodnotilo všechny spočítané náklady.
        """
        meter = self.meter_tariff()
        calendar = self.calendar_tariff()
        if meter is None or calendar is None or self._near_tariff_switch():
            return None

        agrees = meter == calendar
        if agrees:
            self._tariff_mismatch_since = None
        elif self._tariff_mismatch_since is None:
            self._tariff_mismatch_since = dt_util.now()
            _LOGGER.warning(
                "EGD: elektroměr hlásí %s, ale rozvrh HDO očekává %s. "
                "Zkontrolujte kód HDO a výběr relé – náklady se počítají "
                "z kalendáře, takže by vycházely špatně.",
                meter, calendar,
            )
        return agrees

    def next_tariff_change(self) -> datetime | None:
        """Okamžik příští změny tarifu; None při jednotarifu.

        Počítá se z rozvrhu v paměti, bez volání API.
        """
        if self._hdo is None:
            return None
        return self._hdo.next_change(dt_util.now())

    def tariff_after_change(self) -> str | None:
        """Na jaký tarif se při příští změně přepne."""
        current = self.current_tariff()
        if current is None:
            return None
        return "VT" if current == "NT" else "NT"

    def current_price(self) -> float | None:
        """Cena za kWh platná právě teď."""
        now = dt_util.now()
        period = self.price_list.for_date(now.date())
        if period is None:
            return None
        tariff = self.current_tariff()
        if tariff is None:
            # Jednotarif (nebo neznámý rozvrh) – VT je jediná zadaná cena
            return period.price_vt if self._hdo is None else None
        return period.price_nt if tariff == TARIFF_NT else period.price_vt

    async def _quarter_costs(
        self, quarters: dict[datetime, float]
    ) -> dict[datetime, float]:
        """Přepočte čtvrthodinovou spotřebu na náklady v Kč.

        Tarif i cena se vyhodnocují pro každou čtvrthodinu zvlášť. Když rozvrh
        přepíná uprostřed intervalu, rozdělí se energie mezi VT a NT poměrem
        překryvu, takže výsledek sedí i u desetiminutových rozvrhů.
        """
        prices = self.price_list
        if not prices:
            return {}

        hdo = await self._async_hdo()
        costs: dict[datetime, float] = {}
        missing_schedule: set[date] = set()

        for start_utc, energy in quarters.items():
            start_local = start_utc.astimezone(dt_util.DEFAULT_TIME_ZONE)
            period = prices.for_date(start_local.date())
            if period is None:
                # Spotřeba před první zadanou cenou se záměrně neoceňuje
                continue

            if hdo is None:
                nt_fraction = 0.0  # jednotarif – vše za cenu VT
            else:
                nt_fraction = hdo.nt_fraction(start_local, 15)
                if nt_fraction is None:
                    # Rozvrh pro ten den neznáme (např. vypršela jeho platnost).
                    # Radši spotřebu neocenit, než ji celou naúčtovat ve VT.
                    missing_schedule.add(start_local.date())
                    continue

            costs[start_utc] = energy * period.price_for(nt_fraction)

        if missing_schedule:
            _LOGGER.warning(
                "EGD: pro %d dní (%s – %s) není v kalendáři HDO platný rozvrh, "
                "náklady za ně nepočítám. Ověřte kód HDO na hdo.distribuce24.cz/casy.",
                len(missing_schedule), min(missing_schedule), max(missing_schedule),
            )

        return costs

    # ------------------------------------------------------------------
    # Synchronizace dat
    # ------------------------------------------------------------------

    async def _last_complete_day(self, statistic_id: str) -> date | None:
        """Poslední KOMPLETNÍ den dané statistiky v recorderu.

        Statistiky jsou hodinové, takže poslední záznam může být uprostřed dne.
        Za kompletní považujeme den, jehož poslední hodina (23:xx lokálně) je
        uložená – jinak vrátíme den předchozí, aby se zbytek dne dostáhl.
        """
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

    async def _get_last_recorded_date(self) -> date | None:
        """Poslední kompletní den spotřeby v recorderu (None = žádná data)."""
        return await self._last_complete_day(f"{DOMAIN}:{self.ean}_consumption")

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

        def next_missing(last: date | None) -> date:
            return target_from if last is None else last + timedelta(days=1)

        date_from = next_missing(await self._get_last_recorded_date())

        # Náklady mohou zaostávat za spotřebou – typicky když uživatel smaže
        # nákladovou statistiku kvůli přepočtu po opravě ceny. Pak je potřeba
        # sáhnout dál do minulosti, i když je energie kompletní.
        if self.pricing_enabled:
            cost_from = next_missing(
                await self._last_complete_day(self.cost_statistic_id)
            )
            # Nemá smysl počítat náklady pro dny před první zadanou cenou
            first_priced = self.price_list.first_valid_from
            if first_priced is not None:
                cost_from = max(cost_from, first_priced)
            if cost_from < date_from:
                _LOGGER.info(
                    "EGD: náklady zaostávají za spotřebou, dopočítám od %s", cost_from
                )
                date_from = cost_from

        if date_from > date_to:
            _LOGGER.info("EGD: historická data jsou kompletní, přeskakuji")
            return

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

    @staticmethod
    def _group_by_hour(quarters: dict[datetime, float]) -> dict[datetime, float]:
        """Sečte čtvrthodinové hodnoty do celých hodin (klíč v UTC)."""
        hourly: dict[datetime, float] = {}
        for start, value in quarters.items():
            hour = start.replace(minute=0, second=0, microsecond=0)
            hourly[hour] = hourly.get(hour, 0.0) + value
        return hourly

    async def _sync_range(self, date_from: date, date_to: date) -> None:
        """Stáhne a uloží data pro zadaný rozsah dat."""
        try:
            quarter_data = await self.api.async_get_quarter_data(
                self.ean, date_from, date_to, meter_type=self.meter_type
            )
        except EgdPermissionError:
            # Rozsah zasahuje před období, na které má účet oprávnění. API odmítne
            # celý požadavek, i když je mimo jen jeho začátek – zúžíme ho.
            first = await self.api.async_find_first_available_date(
                self.ean, date_from, date_to, meter_type=self.meter_type
            )
            if first is None or first <= date_from:
                raise UpdateFailed(
                    f"EGD: účet nemá oprávnění na data v období {date_from} – {date_to}"
                ) from None
            _LOGGER.warning(
                "EGD: oprávnění na data až od %s (požadováno od %s), zužuji rozsah",
                first, date_from,
            )
            try:
                quarter_data = await self.api.async_get_quarter_data(
                    self.ean, first, date_to, meter_type=self.meter_type
                )
            except EgdApiError as err:
                raise UpdateFailed(f"EGD API chyba: {err}") from err
        except EgdApiError as err:
            raise UpdateFailed(f"EGD API chyba: {err}") from err

        hourly_data = {
            key: self._group_by_hour(quarters) for key, quarters in quarter_data.items()
        }

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

        # Náklady – počítáme z čtvrthodin, ukládáme po hodinách
        await self._sync_costs(quarter_data.get("consumption_kwh", {}))

    async def _sync_costs(self, quarters: dict[datetime, float]) -> None:
        """Spočítá a zapíše nákladové statistiky.

        Zapisují se dvě: samotná cena za energii (do panelu energie) a celkové
        náklady se stálou platbou rozpuštěnou do hodin (odpovídá faktuře).
        """
        if not quarters or not self.pricing_enabled:
            return

        costs = await self._quarter_costs(quarters)
        if not costs:
            return

        hourly_energy = self._group_by_hour(costs)

        written = await self._import_statistics(
            statistic_id=self.cost_statistic_id,
            unit=CURRENCY_CZK,
            name=f"EGD {self.ean} Náklady na spotřebu",
            hourly=hourly_energy,
        )

        await self._import_statistics(
            statistic_id=self.total_cost_statistic_id,
            unit=CURRENCY_CZK,
            name=f"EGD {self.ean} Náklady celkem vč. stálé platby",
            hourly=self._with_standing_charge(hourly_energy),
        )

        self._add_written_costs(written)

    def _add_written_costs(self, written: dict[datetime, float]) -> None:
        """Přičte právě zapsané náklady k běžícím součtům.

        Statistiky se zapisují do fronty recorderu, takže hned po zápisu
        v databázi ještě nejsou. Kdybychom je odtud četli, dostali bychom
        neúplná čísla – proto si součty vedeme sami. Dvojímu započítání
        brání to, že _import_statistics vrací jen skutečně nové hodiny.
        """
        if not written:
            return

        today = dt_util.now().date()
        month_start = today.replace(day=1)
        billing = self.billing_date

        for hour, value in written.items():
            day = hour.astimezone(dt_util.DEFAULT_TIME_ZONE).date()
            if day >= month_start:
                self._month_energy_cost = round(
                    (self._month_energy_cost or 0.0) + value, 4
                )
            if billing is not None and day >= billing:
                self._billing_energy_cost = round(
                    (self._billing_energy_cost or 0.0) + value, 4
                )

    def _with_standing_charge(
        self, hourly_energy: dict[datetime, float]
    ) -> dict[datetime, float]:
        """Přidá k hodinovým nákladům podíl stálé platby.

        Denní část platby se rozpustí rovnoměrně mezi hodiny daného dne, které
        opravdu zapisujeme. Součet za celý den tak vyjde přesně na denní podíl
        a graf nemá skoky – a funguje to i ve dnech s 23 nebo 25 hodinami.
        """
        prices = self.price_list
        by_day: dict[date, list[datetime]] = {}
        for hour in hourly_energy:
            day = hour.astimezone(dt_util.DEFAULT_TIME_ZONE).date()
            by_day.setdefault(day, []).append(hour)

        result = dict(hourly_energy)
        for day, hours in by_day.items():
            fee = prices.daily_standing_charge(day)
            if not fee:
                continue

            # Podíly počítáme jako rozdíly kumulativního součtu, ne jako
            # fee/n dokola. Jinak by se zaokrouhlení každé hodiny sečetlo
            # do systematické odchylky (u 310 Kč/měsíc ~0,3 Kč za rok).
            count = len(hours)
            previous = 0.0
            for index, hour in enumerate(sorted(hours), start=1):
                cumulative = round(fee * index / count, 4)
                result[hour] += cumulative - previous
                previous = cumulative
        return result

    async def _import_statistics(
        self,
        statistic_id: str,
        unit: str,
        name: str,
        hourly: dict[datetime, float],
    ) -> dict[datetime, float]:
        """
        Zapíše hodinové hodnoty do HA recorder jako external statistics.

        Vrací hodiny, které se opravdu zapsaly (bez těch už uložených).

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

        # Vracíme jen skutečně zapsané hodiny. Zápis jde do fronty recorderu
        # a v databázi se objeví až za chvíli, takže volající si z toho musí
        # dopočítat součty sám – zpětné čtení by přišlo příliš brzy.
        return {s["start"]: s["state"] for s in statistics}

    # ------------------------------------------------------------------
    # Stavový objekt pro senzory (aktuální hodnota = včerejšek)
    # ------------------------------------------------------------------

    async def _load_month_cost(self) -> None:
        """Načte náklady za probíhající měsíc z nákladové statistiky.

        Bere se z recorderu, ne z posledního stažení – po restartu HA nemáme
        v paměti nic a měsíc typicky pokrývá víc synchronizací.
        """
        self._month_energy_cost = None
        self._month_key = None

        if not self.pricing_enabled:
            return

        now = dt_util.now()
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        stat_id = self.cost_statistic_id

        stats = await get_instance(self.hass).async_add_executor_job(
            lambda: statistics_during_period(
                self.hass, month_start, None, {stat_id}, "month", None, {"change"}
            )
        )
        rows = stats.get(stat_id) or []
        if not rows:
            return

        change = rows[-1].get("change")
        if change is None:
            return

        self._month_energy_cost = round(float(change), 2)
        self._month_key = (now.year, now.month)

    @property
    def month_standing_charge(self) -> float:
        """Stálá platba naběhlá od začátku měsíce do dneška."""
        today = dt_util.now().date()
        return self.price_list.standing_charge(today.replace(day=1), today)

    @property
    def month_cost_total(self) -> float | None:
        """Náklady za probíhající měsíc včetně naběhlé stálé platby.

        Prvního dne v měsíci ještě nejsou data o spotřebě, ale stálá platba
        známá je – proto vracíme ji, ne „neznámo".
        """
        if not self.pricing_enabled:
            return None
        return round((self._month_energy_cost or 0.0) + self.month_standing_charge, 2)

    @property
    def month_energy_cost(self) -> float | None:
        """Náklady za odebranou energii v probíhajícím měsíci (bez stálé platby)."""
        return self._month_energy_cost

    # ------------------------------------------------------------------
    # Vyúčtování
    # ------------------------------------------------------------------

    @property
    def billing_date(self) -> date | None:
        """Datum posledního vyúčtování z konfigurace."""
        raw = str(self._entry.options.get(CONF_BILLING_DATE, "")).strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            _LOGGER.warning("EGD: neplatné datum vyúčtování %r", raw)
            return None

    @property
    def advance_schedule(self) -> AdvanceSchedule:
        """Rozpis záloh z konfigurace.

        Zálohy se zadávají jen tam, kde se částka mění – stejně jako ceny.
        Ověřeno proti skutečné faktuře: tři řádky rozpisu reprodukovaly všech
        dvanáct plateb za rok včetně součtu na korunu.
        """
        raw = self._entry.options.get(CONF_ADVANCE_PERIODS)
        if raw:
            return AdvanceSchedule.from_options(raw)

        # Převod dřívějšího jednoho pole "měsíční záloha", aby se už zadaná
        # hodnota neztratila – platí od data vyúčtování.
        legacy = self._entry.options.get(CONF_ADVANCE_PAYMENT)
        billing = self.billing_date
        if legacy and billing is not None:
            try:
                return AdvanceSchedule([AdvancePeriod(billing, float(legacy))])
            except (TypeError, ValueError):
                pass
        return AdvanceSchedule([])

    async def _load_billing_cost(self) -> None:
        """Načte náklady od posledního vyúčtování z nákladové statistiky."""
        self._billing_energy_cost = None

        start_date = self.billing_date
        if start_date is None or not self.pricing_enabled:
            return

        start = datetime.combine(
            start_date, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
        stat_id = self.cost_statistic_id
        stats = await get_instance(self.hass).async_add_executor_job(
            lambda: statistics_during_period(
                self.hass, start, None, {stat_id}, "day", None, {"change"}
            )
        )
        rows = stats.get(stat_id) or []
        if not rows:
            self._billing_energy_cost = 0.0
            return

        self._billing_energy_cost = round(
            sum(float(r.get("change") or 0.0) for r in rows), 2
        )

    @property
    def billing_summary(self) -> dict[str, Any] | None:
        """Přehled od posledního vyúčtování; None když datum není zadané."""
        start_date = self.billing_date
        if start_date is None or not self.pricing_enabled:
            return None

        today = dt_util.now().date()
        energy = self._billing_energy_cost or 0.0
        fee = self.price_list.standing_charge(start_date, today)
        total = round(energy + fee, 2)

        schedule = self.advance_schedule
        payments = schedule.payments(start_date, today) if schedule else []
        paid = round(sum(a for _, a in payments), 2)
        upcoming = schedule.next_payment(today) if schedule else None

        return {
            "od": start_date,
            "dny": (today - start_date).days + 1,
            "energie": round(energy, 2),
            "stale_platby": round(fee, 2),
            "celkem": total,
            "pocet_zaloh": len(payments),
            "zaplaceno": paid,
            "posledni_zaloha": payments[-1][0] if payments else None,
            "dalsi_zaloha": upcoming[0] if upcoming else None,
            "dalsi_castka": upcoming[1] if upcoming else None,
            # Kladné číslo = nedoplatek, záporné = přeplatek
            "rozdil": round(total - paid, 2) if schedule else None,
        }

    def _build_state(self) -> dict[str, Any]:
        """Vrátí dict s posledními dostupnými denními hodnotami pro senzory."""
        return {
            "ean": self.ean,
            "last_updated": dt_util.now().isoformat(),
            "values": self._latest_values,
            "dates": {k: v.isoformat() for k, v in self._latest_dates.items()},
            "tariff": self.current_tariff(),
            "tariff_source": self.tariff_source,
            "tariff_agrees": self.check_tariff_agreement(),
            "price": self.current_price(),
            "month_cost": self.month_cost_total,
            "month_energy_cost": self._month_energy_cost,
        }

    # ------------------------------------------------------------------
    # Helper: statistic_id pro daný typ dat
    # ------------------------------------------------------------------

    def get_statistic_id(self, data_key: str) -> str:
        """Vrátí statistic_id pro daný klíč dat."""
        suffix = STAT_DEFINITIONS[data_key][0]
        return f"{DOMAIN}:{self.ean}_{suffix}"
