"""Rozvrh nízkého tarifu (HDO) z veřejného API EG.D.

Endpoint https://hdo.distribuce24.cz/casy vrací **kalendář** spínacích časů,
nikoli aktuální stav signálu. Díky tomu lze tarif určit i zpětně pro libovolný
historický okamžik, což je pro výpočet nákladů z dodatečně stažených dat zásadní.

Struktura záznamu:
    region          PH / SM / TOU / VERSACOM / VYCHOD / ZAPAD
    kodHdo_A        kód pro smart elektroměry (např. "Cd2526_2")
    A, B, DP        kód pro klasické elektroměry (příkazový kód)
    skupinaPovelu   označení povelové skupiny (např. "MO-AKU2")
    sazby[0].sazba  seznam sazeb, na které se rozvrh vztahuje
    sazby[0].dny[]  denVTydnu 1=pondělí … 7=neděle + intervaly nízkého tarifu
    od / do         sezónní platnost; rok "9999" = opakuje se každý rok
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_HDO_URL = "https://hdo.distribuce24.cz/casy"
API_REGION_URL = "https://hdo.distribuce24.cz/region"

MINUTES_PER_DAY = 1440


class HdoError(Exception):
    """Chyba při práci s rozvrhem HDO."""


def _to_minutes(value: str) -> int:
    """Převede "HH:MM:SS" na minuty od půlnoci.

    Konec dne je v datech zapsaný dvěma způsoby – "23:59:00" i "00:00:00".
    Obojí normalizujeme na 1440, aby poslední minuta dne nevypadla z intervalu.
    """
    try:
        hours, minutes = int(value[0:2]), int(value[3:5])
    except (ValueError, IndexError) as err:
        raise HdoError(f"Neplatný čas v rozvrhu HDO: {value!r}") from err

    total = hours * 60 + minutes
    if total in (0, 23 * 60 + 59):
        # 00:00 jako konec intervalu i 23:59 znamenají konec dne
        return MINUTES_PER_DAY if total else 0
    return total


def _in_season(record: dict[str, Any], day: date) -> bool:
    """Platí záznam pro zadané datum podle sezónního rozsahu od–do?"""
    od, do = record.get("od") or {}, record.get("do") or {}
    try:
        od_m, od_d = int(od["mesic"]), int(od["den"])
        do_m, do_d = int(do["mesic"]), int(do["den"])
        od_y, do_y = od["rok"], do["rok"]
    except (KeyError, ValueError, TypeError):
        return False

    if od_y == "9999" and do_y == "9999":
        # Opakuje se každý rok – porovnáváme jen měsíc a den.
        start, end, current = (od_m, od_d), (do_m, do_d), (day.month, day.day)
        if start <= end:
            return start <= current <= end
        # Rozsah přes přelom roku, např. 1.10. – 31.3.
        return current >= start or current <= end

    try:
        return date(int(od_y), od_m, od_d) <= day <= date(int(do_y), do_m, do_d)
    except ValueError:
        return False


@dataclass(frozen=True)
class HdoVariant:
    """Jedna rozlišitelná varianta rozvrhu – u klasických kódů jich může být víc."""

    key: str
    sazba: str
    skupina_povelu: str
    region: str
    # Hodin nízkého tarifu za den v aktuálně platné sezóně (None = nezjištěno)
    nt_hours: float | None = None

    @property
    def label(self) -> str:
        """Popisek pro výběr v konfiguraci.

        Počet hodin je tu podstatný: jeden kód často řídí i vedlejší obvod
        (typicky TUV – bojler), který má výrazně méně hodin a tarif neurčuje.
        Rozvrh s nejvíc hodinami odpovídá sazbě a je ten správný.
        """
        base = f"{self.sazba} ({self.skupina_povelu}, {self.region})"
        if self.nt_hours is None:
            return base
        return f"{base} – {self.nt_hours:g} h NT/den"


def variant_of(
    record: dict[str, Any], nt_hours: float | None = None
) -> HdoVariant:
    """Vytvoří identifikátor varianty ze záznamu."""
    sazba = (record.get("sazby") or [{}])[0].get("sazba", "")
    skupina = record.get("skupinaPovelu", "")
    region = record.get("region", "")
    return HdoVariant(
        key=f"{region}|{skupina}|{sazba}",
        sazba=sazba,
        skupina_povelu=skupina,
        region=region,
        nt_hours=nt_hours,
    )


class HdoSchedule:
    """Vyhodnocuje nízký tarif pro jeden konkrétní HDO kód.

    Drží už vyfiltrované záznamy (jeden kód, jedna varianta, různé sezóny).
    """

    def __init__(self, records: list[dict[str, Any]]) -> None:
        if not records:
            raise HdoError("Rozvrh HDO neobsahuje žádný záznam")
        self._records = records

    def _windows_for(self, day: date) -> list[tuple[int, int]] | None:
        """Intervaly nízkého tarifu (v minutách od půlnoci) pro zadaný den.

        Vrací None, pokud pro daný den v kalendáři není platný záznam – to je
        něco jiného než prázdný seznam (den, kdy nízký tarif prostě není).
        Rozlišení je podstatné: část rozvrhů má konkrétní rok platnosti, a po
        jeho vypršení bychom jinak mlčky účtovali všechno ve vysokém tarifu.
        """
        weekday = day.isoweekday()  # 1 = pondělí, shodně s denVTydnu
        matched_season = False

        for record in self._records:
            if not _in_season(record, day):
                continue
            matched_season = True
            for sazba in record.get("sazby") or []:
                for den in sazba.get("dny") or []:
                    if den.get("denVTydnu") != weekday:
                        continue
                    return [
                        (_to_minutes(c["od"]), _to_minutes(c["do"]))
                        for c in den.get("casy") or []
                        if c.get("od") and c.get("do")
                    ]

        # Sezónní záznam existuje, ale pro tento den v týdnu žádné okno nemá –
        # API takové dny prostě vynechá (víkendová sazba D61d uvádí jen pá–ne).
        # To znamená „celý den vysoký tarif“, nikoli neznámý rozvrh.
        return [] if matched_season else None

    def has_schedule_for(self, day: date) -> bool:
        """Je pro zadaný den v kalendáři platný rozvrh?"""
        return self._windows_for(day) is not None

    def nt_fraction(
        self, start_local: datetime, duration_minutes: int
    ) -> float | None:
        """Jaká část intervalu spadá do nízkého tarifu (0.0 – 1.0).

        Některé rozvrhy přepínají na desetiminutách (07:20, 08:10), takže
        čtvrthodina může být rozdělená mezi oba tarify. Vracíme proto poměr,
        ne binární příznak – náklady se pak rozdělí přesně.

        None znamená, že pro daný den není znám rozvrh; takovou spotřebu je
        lepší neocenit než ocenit špatně.
        """
        if duration_minutes <= 0:
            return 0.0

        windows = self._windows_for(start_local.date())
        if windows is None:
            return None

        begin = start_local.hour * 60 + start_local.minute
        end = begin + duration_minutes
        overlap = 0

        for win_start, win_end in windows:
            overlap += max(0, min(end, win_end) - max(begin, win_start))

        # Přesah přes půlnoc (u čtvrthodin nenastává, ale ať je metoda obecná)
        if end > MINUTES_PER_DAY:
            next_day = date.fromordinal(start_local.date().toordinal() + 1)
            for win_start, win_end in self._windows_for(next_day) or []:
                overlap += max(0, min(end - MINUTES_PER_DAY, win_end) - win_start)

        return min(1.0, overlap / duration_minutes)

    def nt_hours_per_day(self, day: date) -> float | None:
        """Kolik hodin nízkého tarifu má zadaný den (None = rozvrh není znám)."""
        windows = self._windows_for(day)
        if windows is None:
            return None
        return round(sum(max(0, e - s) for s, e in windows) / 60, 2)

    def is_low_tariff(self, moment_local: datetime) -> bool | None:
        """Je v daném okamžiku nízký tarif? None = rozvrh pro ten den není znám."""
        windows = self._windows_for(moment_local.date())
        if windows is None:
            return None
        minute = moment_local.hour * 60 + moment_local.minute
        return any(start <= minute < end for start, end in windows)

    def differs_from(self, other: HdoSchedule, around: date, days: int = 14) -> bool:
        """Liší se rozvrhy v okolí zadaného data?

        Porovnáváme vypočtená okna, ne surové záznamy – distributor může
        přeuspořádat data, aniž by se změnily skutečné časy.
        """
        for offset in range(-days, days + 1):
            day = around + timedelta(days=offset)
            if self._windows_for(day) != other._windows_for(day):
                return True
        return False

    def next_change(
        self, after_local: datetime, max_days: int = 14
    ) -> datetime | None:
        """Nejbližší okamžik po `after_local`, kdy se tarif přepne.

        Kandidáty jsou začátky a konce oken plus půlnoc (rozvrh se liší podle
        dne v týdnu i sezóny). Prohledáváme dopředu po dnech, protože při
        celodenním tarifu může být další změna až za několik dní.
        """
        current = self.is_low_tariff(after_local)
        if current is None:
            return None

        tzinfo = after_local.tzinfo
        start_day = after_local.date()

        for offset in range(max_days + 1):
            day = start_day + timedelta(days=offset)

            marks = {0}  # půlnoc – tam se rozvrh může změnit i beze změny okna
            for win_start, win_end in self._windows_for(day) or []:
                marks.add(win_start)
                if win_end < MINUTES_PER_DAY:
                    marks.add(win_end)

            for minute in sorted(marks):
                moment = datetime.combine(
                    day, time(minute // 60, minute % 60), tzinfo=tzinfo
                )
                if moment <= after_local:
                    continue
                state = self.is_low_tariff(moment)
                if state is not None and state != current:
                    return moment

        return None


class HdoClient:
    """Stahuje a filtruje rozvrhy HDO z veřejného API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._records: list[dict[str, Any]] | None = None
        self._records_date: date | None = None
        self._regions: list[dict[str, Any]] | None = None

    async def _fetch_json(self, url: str) -> Any:
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                resp.raise_for_status()
                # Endpoint hlásí text/plain, proto content_type=None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as err:
            raise HdoError(f"Nelze stáhnout {url}: {err}") from err

    async def async_records(self) -> list[dict[str, Any]]:
        """Vrátí rozvrhy; stahuje nejvýš jednou denně (mění se zřídka)."""
        today = date.today()
        if self._records is not None and self._records_date == today:
            return self._records

        data = await self._fetch_json(API_HDO_URL)
        if not isinstance(data, list) or not data:
            raise HdoError("API rozvrhů HDO vrátilo neočekávaná data")

        self._records = data
        self._records_date = today
        _LOGGER.debug("EGD HDO: staženo %d rozvrhů", len(data))
        return data

    async def async_region_for_psc(self, psc: str) -> str:
        """Přeloží PSČ na region (potřebné jen pro klasické kódy A/B/DP)."""
        if self._regions is None:
            data = await self._fetch_json(API_REGION_URL)
            if not isinstance(data, list):
                raise HdoError("API regionů vrátilo neočekávaná data")
            self._regions = data

        normalized = psc.replace(" ", "")
        for item in self._regions:
            if str(item.get("PSC", "")).replace(" ", "") == normalized:
                return str(item["Region"])
        raise HdoError(f"PSČ {psc} nebylo nalezeno v číselníku regionů")

    async def async_match_smart(self, code: str) -> list[dict[str, Any]]:
        """Záznamy pro smart kód (kodHdo_A). Ty jsou napříč regiony jednoznačné."""
        records = await self.async_records()
        return [r for r in records if r.get("kodHdo_A") == code]

    async def async_match_classic(
        self, psc: str, code_a: str, code_b: str, code_dp: str
    ) -> list[dict[str, Any]]:
        """Záznamy pro klasický příkazový kód A/B/DP v regionu dle PSČ."""
        region = await self.async_region_for_psc(psc)
        records = await self.async_records()
        return [
            r
            for r in records
            if r.get("region") == region
            and str(r.get("A")) == str(code_a)
            and str(r.get("B")) == str(code_b)
            # DP bývá zapsané i s vedoucí nulou ("6" vs "06")
            and str(r.get("DP")).lstrip("0") == str(code_dp).lstrip("0")
        ]

    @staticmethod
    def variants(records: list[dict[str, Any]]) -> list[HdoVariant]:
        """Rozlišitelné varianty v sadě záznamů (u klasických kódů i více relé).

        Ke každé doplní počet hodin nízkého tarifu v právě platné sezóně,
        aby šlo v konfiguraci poznat tarifní relé od vedlejšího obvodu.
        Řadí se sestupně – tarifní rozvrh má hodin nejvíc.
        """
        today = date.today()
        seen: dict[str, HdoVariant] = {}

        for record in records:
            key = variant_of(record).key
            if key in seen or not _in_season(record, today):
                continue
            try:
                hours = HdoSchedule([record]).nt_hours_per_day(today)
            except HdoError:
                hours = None
            seen[key] = variant_of(record, hours)

        # Záznamy mimo aktuální sezónu doplníme bez počtu hodin
        for record in records:
            variant = variant_of(record)
            seen.setdefault(variant.key, variant)

        return sorted(
            seen.values(), key=lambda v: (v.nt_hours is None, -(v.nt_hours or 0))
        )

    @staticmethod
    def schedule_for(
        records: list[dict[str, Any]], variant_key: str | None = None
    ) -> HdoSchedule:
        """Sestaví rozvrh; při více variantách vybere tu zadanou."""
        if variant_key:
            records = [r for r in records if variant_of(r).key == variant_key]
        return HdoSchedule(records)
