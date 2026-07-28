"""EG.D Distribuce API klient."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from .const import (
    API_DATA_URL,
    API_DATA_URL_TEST,
    API_OM_URL,
    API_OM_URL_TEST,
    API_SCOPE,
    API_TOKEN_URL,
    API_TOKEN_URL_TEST,
    METER_TYPE_AB,
    METER_TYPE_C1,
    PROFILE_C1_CONSUMPTION,
    PROFILE_C1_PRODUCTION,
    PROFILE_C1_PRODUCTION_SHARING,
    PROFILE_C1_SHARING_COMMERCIAL,
    PROFILE_C1_SHARING_DISTRIBUTION,
    PROFILE_ICC1,
    PROFILE_ICQ2,
    PROFILE_ICQD,
    PROFILE_ICQS,
    PROFILE_IKC1,
    PROFILE_IKC2,
    PROFILE_IMC1,
    PROFILE_IMQ2,
    PROFILE_ISC1,
    PROFILE_ISQ2,
    PROFILE_ISQS,
    STATUS_VALID,
)

_LOGGER = logging.getLogger(__name__)

# Datum zavedení kWh profilů pro typ A/B
KWH_PROFILES_SINCE = date(2024, 7, 1)

# Počet záznamů na jednu stránku API (96 čtvrthodin = 1 den → 3000 ≈ 31 dní)
PAGE_SIZE = 3000
# Pojistka proti nekonečné smyčce (≈ 10 let čtvrthodinových dat)
MAX_RECORDS = 400_000


class EgdApiError(Exception):
    """Obecná chyba API."""


class EgdAuthError(EgdApiError):
    """Chyba autentizace (401)."""


class EgdPermissionError(EgdApiError):
    """Oprávnění pro EAN nebo období odmítnuto (400 validation_error)."""


class EgdUnsupportedMeterError(EgdApiError):
    """Typ měřiče není podporován API (např. C4)."""


class EgdApi:
    """Klient pro EG.D Distribuce OpenAPI."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
        test_mode: bool = False,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._test_mode = test_mode
        self._token: str | None = None
        self._token_date: date | None = None
        # URL volíme dle režimu
        self._token_url = API_TOKEN_URL_TEST if test_mode else API_TOKEN_URL
        self._data_url  = API_DATA_URL_TEST  if test_mode else API_DATA_URL
        self._om_url    = API_OM_URL_TEST    if test_mode else API_OM_URL

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Vrátí platný token – pokud je ze dneška, použije cached."""
        today = date.today()
        if self._token and self._token_date == today:
            return self._token

        _LOGGER.debug("EGD: získávám nový token")
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": API_SCOPE,
        }
        try:
            async with self._session.post(
                self._token_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401:
                    raise EgdAuthError("Neplatný client_id nebo client_secret")
                resp.raise_for_status()
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise EgdApiError(f"Chyba při získávání tokenu: {err}") from err

        token = data.get("access_token")
        if not token:
            raise EgdApiError("API nevrátilo access_token")

        self._token = token
        self._token_date = today
        _LOGGER.debug("EGD: token úspěšně získán (platný do půlnoci)")
        return token

    # ------------------------------------------------------------------
    # Načtení dat pro jeden profil – jednotný endpoint pro A/B i C1
    # ------------------------------------------------------------------

    async def _request_page(
        self,
        ean: str,
        profile: str,
        from_str: str,
        to_str: str,
        page_start: int,
    ) -> list[dict[str, Any]]:
        """Provede jedno GET volání na /rest/spotreby pro zadaný rozsah a offset."""
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}

        params = {
            "ean": ean,
            "profile": profile,
            "from": from_str,
            "to": to_str,
            "pageStart": str(page_start),
            "pageSize": str(PAGE_SIZE),
        }

        _LOGGER.debug("EGD: GET %s params=%s", self._data_url, params)
        try:
            async with self._session.get(
                self._data_url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 401:
                    self._token = None
                    self._token_date = None
                    raise EgdAuthError("Token odmítnut, zkus znovu")
                if not resp.ok:
                    body = await resp.text()
                    _LOGGER.error(
                        "EGD: HTTP %s pro profil %s (%s–%s): %s",
                        resp.status, profile, from_str, to_str, body,
                    )
                    try:
                        err_data = json.loads(body)
                        if err_data.get("error") == "validation_error":
                            raise EgdPermissionError(err_data.get("message", body))
                    except (ValueError, KeyError, AttributeError):
                        pass
                    resp.raise_for_status()
                data = await resp.json()
        except (EgdAuthError, EgdPermissionError):
            raise
        except aiohttp.ClientError as err:
            raise EgdApiError(f"Chyba při stahování profilu {profile}: {err}") from err

        # Produkční prostředí vrací přímo objekt {"data": [...]},
        # testovací prostředí vrací pole [{"data": [...]}].
        if isinstance(data, list):
            if not data:
                return []
            return data[0].get("data", [])
        if isinstance(data, dict):
            return data.get("data", [])
        return []

    async def _get_profile_data(
        self,
        ean: str,
        profile: str,
        from_str: str,
        to_str: str,
    ) -> list[dict[str, Any]]:
        """Stáhne všechny záznamy pro zadaný rozsah včetně stránkování.

        API omezuje počet vrácených záznamů na pageSize a v odpovědi nijak
        nesignalizuje, že existují další – pole "total" je jen počet záznamů
        na aktuální stránce. Proto čteme dál, dokud stránka přijde plná.
        Parametr pageStart je 1-based offset záznamu, nikoli číslo stránky.
        """
        records: list[dict[str, Any]] = []
        page_start = 1

        while True:
            batch = await self._request_page(ean, profile, from_str, to_str, page_start)
            records.extend(batch)

            if len(batch) < PAGE_SIZE:
                break

            page_start += PAGE_SIZE
            if page_start > MAX_RECORDS:
                _LOGGER.warning(
                    "EGD: profil %s (%s–%s) překročil limit %d záznamů, data mohou být neúplná",
                    profile, from_str, to_str, MAX_RECORDS,
                )
                break

        return records

    async def _fetch_profile(
        self,
        ean: str,
        profile: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Stáhne čtvrthodinová data z jednotného endpointu /rest/spotreby."""
        # API používá exkluzivní dolní mez a vrací intervaly, jejichž START < to.
        # Aby byl zahrnut interval 00:00 prvního dne, musí from být o 15 min dřív.
        # Aby byl zahrnut interval 23:45 posledního dne, musí to být 23:59.
        to_str = f"{date_to.isoformat()}T23:59:00.000"
        shifted_from_str = f"{(date_from - timedelta(days=1)).isoformat()}T23:45:00.000"

        try:
            return await self._get_profile_data(ean, profile, shifted_from_str, to_str)
        except EgdPermissionError:
            # Pokud den_from je úplně první den, na který má účet oprávnění,
            # posun o 23:45 předchozího dne spadne mimo autorizované období
            # a API vrátí tvrdou chybu (nikoli jen prázdná data). Zkusíme to
            # znovu bez posunu – přijdeme jen o první čtvrthodinu (00:00–00:15).
            unshifted_from_str = f"{date_from.isoformat()}T00:00:00.000"
            _LOGGER.debug(
                "EGD: profil %s odmítnut pro from=%s, zkouším bez posunu (from=%s)",
                profile, shifted_from_str, unshifted_from_str,
            )
            return await self._get_profile_data(ean, profile, unshifted_from_str, to_str)

    # ------------------------------------------------------------------
    # Agregace – součet čtvrthodin za každý den
    # ------------------------------------------------------------------

    def _aggregate_hourly(
        self,
        records: list[dict[str, Any]],
        is_power_kw: bool,
    ) -> dict[datetime, float]:
        """
        Z čtvrthodinových záznamů vypočítá hodinové součty.

        Klíčem je začátek hodiny v UTC – statistiky HA se ukládají po hodinách
        a jako UTC timestampy, takže bucketování v UTC je korektní i přes
        přechody letního času.

        Platný status je W pro všechny typy měřičů (dle dokumentace 2026-05).
        Pro kW profily (ICC1, ISC1): hodnota kW ÷ 4 = kWh za čtvrthodinu.
        Pro kWh profily (ICQ2, ISQ2, DCQC, DSQC): hodnoty jsou již energie.
        """
        hourly: dict[datetime, float] = {}

        for rec in records:
            if rec.get("status") != STATUS_VALID:
                continue

            value = rec.get("value")
            if value is None:
                continue

            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            ts_str = rec.get("timestamp", "")
            try:
                dt_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                hour_start = dt_utc.replace(minute=0, second=0, microsecond=0)
            except (ValueError, TypeError, AttributeError):
                continue

            energy = value / 4.0 if is_power_kw else value
            hourly[hour_start] = hourly.get(hour_start, 0.0) + energy

        return hourly

    # ------------------------------------------------------------------
    # Načtení denních dat dle typu měřiče
    # ------------------------------------------------------------------

    async def _get_hourly_data_ab(
        self,
        ean: str,
        date_from: date,
        date_to: date,
    ) -> dict[str, dict[datetime, float]]:
        """Stáhne a agreguje hodinová data pro typ A/B."""
        use_kwh = date_from >= KWH_PROFILES_SINCE
        result: dict[str, dict[datetime, float]] = {}

        # --- Spotřeba ze sítě: ICQ2 (kWh) nebo ICC1 (kW ÷ 4) ---
        consumption_profile = PROFILE_ICQ2 if use_kwh else PROFILE_ICC1
        records = await self._fetch_profile(ean, consumption_profile, date_from, date_to)
        if not records and use_kwh:
            _LOGGER.debug("EGD A/B: ICQ2 prázdné, fallback na ICC1")
            records = await self._fetch_profile(ean, PROFILE_ICC1, date_from, date_to)
            result["consumption_kwh"] = self._aggregate_hourly(records, is_power_kw=True)
        else:
            result["consumption_kwh"] = self._aggregate_hourly(records, is_power_kw=not use_kwh)

        # --- Dodávka do sítě: ISQ2 (kWh) nebo ISC1 (kW ÷ 4) ---
        production_profile = PROFILE_ISQ2 if use_kwh else PROFILE_ISC1
        records = await self._fetch_profile(ean, production_profile, date_from, date_to)
        if not records and use_kwh:
            _LOGGER.debug("EGD A/B: ISQ2 prázdné, fallback na ISC1")
            records = await self._fetch_profile(ean, PROFILE_ISC1, date_from, date_to)
            result["production_kwh"] = self._aggregate_hourly(records, is_power_kw=True)
        else:
            result["production_kwh"] = self._aggregate_hourly(records, is_power_kw=not use_kwh)

        # --- Sdílení energie: ICQS (obchodní) a ICQD (distribuční) ---
        try:
            records = await self._fetch_profile(ean, PROFILE_ICQS, date_from, date_to)
            result["sharing_commercial_kwh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD A/B: EAN %s nemá profil ICQS (sdílení obchodní), přeskakuji", ean)
            result["sharing_commercial_kwh"] = {}

        try:
            records = await self._fetch_profile(ean, PROFILE_ICQD, date_from, date_to)
            result["sharing_distribution_kwh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD A/B: EAN %s nemá profil ICQD (sdílení distribuční), přeskakuji", ean)
            result["sharing_distribution_kwh"] = {}

        # --- Dodávka ponížená v rámci sdílení: ISQS ---
        try:
            records = await self._fetch_profile(ean, PROFILE_ISQS, date_from, date_to)
            result["production_sharing_kwh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD A/B: EAN %s nemá profil ISQS (dodávka ponížená sdílením), přeskakuji", ean)
            result["production_sharing_kwh"] = {}

        # --- Jalová spotřeba: IKC2 (preferováno), fallback IKC1 ---
        try:
            records = await self._fetch_profile(ean, PROFILE_IKC2, date_from, date_to)
            result["reactive_consumption_kvarh"] = self._aggregate_hourly(records, is_power_kw=True)
        except EgdPermissionError:
            try:
                _LOGGER.debug("EGD A/B: IKC2 nedostupné, zkouším IKC1")
                records = await self._fetch_profile(ean, PROFILE_IKC1, date_from, date_to)
                result["reactive_consumption_kvarh"] = self._aggregate_hourly(records, is_power_kw=True)
            except EgdPermissionError:
                _LOGGER.debug("EGD A/B: EAN %s nemá profil IKC2 ani IKC1 (jalová spotřeba), přeskakuji", ean)
                result["reactive_consumption_kvarh"] = {}

        # --- Jalová dodávka: IMQ2 (kWh, preferováno), fallback IMC1 (kW ÷ 4) ---
        try:
            records = await self._fetch_profile(ean, PROFILE_IMQ2, date_from, date_to)
            result["reactive_production_kvarh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            try:
                _LOGGER.debug("EGD A/B: IMQ2 nedostupné, zkouším IMC1")
                records = await self._fetch_profile(ean, PROFILE_IMC1, date_from, date_to)
                result["reactive_production_kvarh"] = self._aggregate_hourly(records, is_power_kw=True)
            except EgdPermissionError:
                _LOGGER.debug("EGD A/B: EAN %s nemá profil IMQ2 ani IMC1 (jalová dodávka), přeskakuji", ean)
                result["reactive_production_kvarh"] = {}

        _LOGGER.debug(
            "EGD A/B: stažena data %s–%s, spotřeba %d hodin, výroba %d hodin",
            date_from, date_to,
            len(result["consumption_kwh"]),
            len(result["production_kwh"]),
        )
        return result

    @staticmethod
    def _records_are_identical(
        records_a: list[dict[str, Any]],
        records_b: list[dict[str, Any]],
    ) -> bool:
        """Vrátí True, pokud jsou oba seznamy záznamů po hodnotách identické.

        API pro EAN bez výroby vrací pro DSQC stejná data jako pro DCQC.
        Tímto porovnáním detekujeme zrcadlení a nenastavíme chybnou výrobu.
        """
        if len(records_a) != len(records_b):
            return False
        return all(
            a.get("timestamp") == b.get("timestamp") and a.get("value") == b.get("value")
            for a, b in zip(records_a, records_b)
        )

    async def _get_hourly_data_c1(
        self,
        ean: str,
        date_from: date,
        date_to: date,
    ) -> dict[str, dict[datetime, float]]:
        """Stáhne a agreguje hodinová data pro typ C1 (profily DCQC/DSQC/DCQS/DCQD)."""
        result: dict[str, dict[datetime, float]] = {}

        dcqc_records = await self._fetch_profile(ean, PROFILE_C1_CONSUMPTION, date_from, date_to)
        result["consumption_kwh"] = self._aggregate_hourly(dcqc_records, is_power_kw=False)

        # DSQC – dodávka do sítě; EAN bez výroby vrací API identická data jako DCQC
        try:
            dsqc_records = await self._fetch_profile(ean, PROFILE_C1_PRODUCTION, date_from, date_to)
            if self._records_are_identical(dcqc_records, dsqc_records):
                _LOGGER.debug(
                    "EGD C1: EAN %s – DSQC identické s DCQC, EAN nemá dodávku do sítě",
                    ean,
                )
                result["production_kwh"] = {}
            else:
                result["production_kwh"] = self._aggregate_hourly(dsqc_records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD C1: EAN %s nemá profil DSQC (dodávka do sítě), přeskakuji", ean)
            result["production_kwh"] = {}

        # DCQS – sdílení energie (obchodní část)
        try:
            records = await self._fetch_profile(ean, PROFILE_C1_SHARING_COMMERCIAL, date_from, date_to)
            result["sharing_commercial_kwh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD C1: EAN %s nemá profil DCQS (sdílení obchodní), přeskakuji", ean)
            result["sharing_commercial_kwh"] = {}

        # DCQD – sdílení energie (distribuční část)
        try:
            records = await self._fetch_profile(ean, PROFILE_C1_SHARING_DISTRIBUTION, date_from, date_to)
            result["sharing_distribution_kwh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD C1: EAN %s nemá profil DCQD (sdílení distribuční), přeskakuji", ean)
            result["sharing_distribution_kwh"] = {}

        # DSQS – dodávka ponížená v rámci sdílení
        try:
            records = await self._fetch_profile(ean, PROFILE_C1_PRODUCTION_SHARING, date_from, date_to)
            result["production_sharing_kwh"] = self._aggregate_hourly(records, is_power_kw=False)
        except EgdPermissionError:
            _LOGGER.debug("EGD C1: EAN %s nemá profil DSQS (dodávka ponížená sdílením), přeskakuji", ean)
            result["production_sharing_kwh"] = {}

        # C1 nemá jalová měření
        result["reactive_consumption_kvarh"] = {}
        result["reactive_production_kvarh"] = {}

        _LOGGER.debug(
            "EGD C1: stažena data %s–%s, spotřeba %d hodin, výroba %d hodin, sdílení-ob %d hodin, sdílení-dis %d hodin",
            date_from, date_to,
            len(result["consumption_kwh"]),
            len(result["production_kwh"]),
            len(result["sharing_commercial_kwh"]),
            len(result["sharing_distribution_kwh"]),
        )
        return result

    # ------------------------------------------------------------------
    # Veřejné metody
    # ------------------------------------------------------------------

    async def async_validate_credentials(self, ean: str) -> str:
        """
        Ověří přihlašovací údaje a zjistí typ měřiče z /rest/om.

        Vrátí METER_TYPE_AB nebo METER_TYPE_C1.
        Raises EgdAuthError, EgdPermissionError nebo EgdUnsupportedMeterError.
        """
        await self._ensure_token()
        return await self.async_detect_meter_type(ean)

    async def async_get_hourly_data(
        self,
        ean: str,
        date_from: date,
        date_to: date,
        meter_type: str = METER_TYPE_AB,
    ) -> dict[str, dict[datetime, float]]:
        """Stáhne a agreguje hodinová data dle typu měřiče."""
        if meter_type == METER_TYPE_C1:
            return await self._get_hourly_data_c1(ean, date_from, date_to)
        return await self._get_hourly_data_ab(ean, date_from, date_to)

    def _consumption_profile(self, meter_type: str, day: date) -> str:
        """Profil spotřeby pro daný typ měřiče (u A/B dle data zavedení kWh profilů)."""
        if meter_type == METER_TYPE_C1:
            return PROFILE_C1_CONSUMPTION
        return PROFILE_ICQ2 if day >= KWH_PROFILES_SINCE else PROFILE_ICC1

    async def async_find_first_available_date(
        self,
        ean: str,
        date_from: date,
        date_to: date,
        meter_type: str = METER_TYPE_AB,
    ) -> date | None:
        """Najde nejstarší den v rozsahu, na který má účet oprávnění na data.

        API odmítne celý požadavek chybou 400, i když je mimo autorizované
        období jen jeho začátek. Oprávnění tvoří souvislé období, takže
        nejstarší dostupný den najdeme binárním půlením (~log2(N) dotazů).

        Vrací None, pokud nejsou dostupná data ani pro date_to.
        """

        async def authorized(day: date) -> bool:
            try:
                await self._fetch_profile(
                    ean, self._consumption_profile(meter_type, day), day, day
                )
            except EgdPermissionError:
                return False
            return True

        if await authorized(date_from):
            return date_from
        if not await authorized(date_to):
            return None

        # lo = známý neautorizovaný den, hi = známý autorizovaný den
        lo, hi = date_from, date_to
        while (hi - lo).days > 1:
            mid = lo + timedelta(days=(hi - lo).days // 2)
            if await authorized(mid):
                hi = mid
            else:
                lo = mid
        return hi

    async def async_get_om_list(self) -> list[dict[str, str]]:
        """Vrátí seznam odběrných míst s typem měření z /rest/om."""
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with self._session.get(
                self._om_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if not resp.ok:
                    _LOGGER.warning("EGD: /rest/om vrátil HTTP %s", resp.status)
                    return []
                return await resp.json()
        except aiohttp.ClientError as err:
            _LOGGER.warning("EGD: chyba při volání /rest/om: %s", err)
            return []

    async def async_detect_meter_type(self, ean: str) -> str:
        """
        Zjistí typ měřiče z /rest/om pro daný EAN.

        Raises:
            EgdUnsupportedMeterError: pokud je typ C4 nebo jiný nepodporovaný
            EgdPermissionError: pokud EAN není v seznamu (není propojen s API účtem)
        """
        om_list = await self.async_get_om_list()

        for item in om_list:
            if item.get("ean") == ean:
                typ = item.get("typMereni", "").upper()
                if typ in ("A", "B"):
                    return METER_TYPE_AB
                if typ == "C1":
                    return METER_TYPE_C1
                raise EgdUnsupportedMeterError(
                    f"Typ měřiče {typ} není podporován – API poskytuje data pouze pro typy A, B a C1"
                )

        raise EgdPermissionError(
            f"EAN {ean} nebyl nalezen v seznamu odběrných míst – ověřte EAN nebo propojení API účtu"
        )
