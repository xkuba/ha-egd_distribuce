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
    PROFILE_ICC1,
    PROFILE_ICQ2,
    PROFILE_IKC1,
    PROFILE_IMC1,
    PROFILE_ISC1,
    PROFILE_ISQ2,
    STATUS_VALID,
)

_LOGGER = logging.getLogger(__name__)

# Datum zavedení kWh profilů pro typ A/B
KWH_PROFILES_SINCE = date(2024, 7, 1)


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

    async def _fetch_profile(
        self,
        ean: str,
        profile: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Stáhne čtvrthodinová data z jednotného endpointu /rest/spotreby."""
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}

        from_str = f"{date_from.isoformat()}T00:00:00.000"
        to_str = f"{date_to.isoformat()}T23:45:00.000"

        params = {
            "ean": ean,
            "profile": profile,
            "from": from_str,
            "to": to_str,
            "pageStart": "1",
            "pageSize": "3000",
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
                        resp.status, profile, date_from, date_to, body,
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

        if not data or not isinstance(data, list):
            return []

        return data[0].get("data", [])

    # ------------------------------------------------------------------
    # Agregace – součet čtvrthodin za každý den
    # ------------------------------------------------------------------

    def _aggregate_daily(
        self,
        records: list[dict[str, Any]],
        is_power_kw: bool,
    ) -> dict[date, float]:
        """
        Z čtvrthodinových záznamů vypočítá denní součty.

        Platný status je W pro všechny typy měřičů (dle dokumentace 2026-05).
        Pro kW profily (ICC1, ISC1): hodnota kW ÷ 4 = kWh za čtvrthodinu.
        Pro kWh profily (ICQ2, ISQ2, DCQC, DSQC): hodnoty jsou již energie.
        """
        daily: dict[date, float] = {}

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
                dt_local = dt_utc.astimezone()
                day = dt_local.date()
            except (ValueError, TypeError):
                continue

            energy = value / 4.0 if is_power_kw else value
            daily[day] = daily.get(day, 0.0) + energy

        return daily

    # ------------------------------------------------------------------
    # Načtení denních dat dle typu měřiče
    # ------------------------------------------------------------------

    async def _get_daily_data_ab(
        self,
        ean: str,
        date_from: date,
        date_to: date,
    ) -> dict[str, dict[date, float]]:
        """Stáhne a agreguje denní data pro typ A/B."""
        use_kwh = date_from >= KWH_PROFILES_SINCE
        result: dict[str, dict[date, float]] = {}

        consumption_profile = PROFILE_ICQ2 if use_kwh else PROFILE_ICC1
        records = await self._fetch_profile(ean, consumption_profile, date_from, date_to)
        if not records and use_kwh:
            _LOGGER.debug("EGD: ICQ2 prázdné, fallback na ICC1")
            records = await self._fetch_profile(ean, PROFILE_ICC1, date_from, date_to)
            result["consumption_kwh"] = self._aggregate_daily(records, is_power_kw=True)
        else:
            result["consumption_kwh"] = self._aggregate_daily(
                records, is_power_kw=not use_kwh
            )

        production_profile = PROFILE_ISQ2 if use_kwh else PROFILE_ISC1
        records = await self._fetch_profile(ean, production_profile, date_from, date_to)
        if not records and use_kwh:
            _LOGGER.debug("EGD: ISQ2 prázdné, fallback na ISC1")
            records = await self._fetch_profile(ean, PROFILE_ISC1, date_from, date_to)
            result["production_kwh"] = self._aggregate_daily(records, is_power_kw=True)
        else:
            result["production_kwh"] = self._aggregate_daily(
                records, is_power_kw=not use_kwh
            )

        try:
            records = await self._fetch_profile(ean, PROFILE_IKC1, date_from, date_to)
            result["reactive_consumption_kvarh"] = self._aggregate_daily(records, is_power_kw=True)
        except EgdPermissionError:
            _LOGGER.debug("EGD: EAN %s nemá profil IKC1 (jalová spotřeba), přeskakuji", ean)
            result["reactive_consumption_kvarh"] = {}

        try:
            records = await self._fetch_profile(ean, PROFILE_IMC1, date_from, date_to)
            result["reactive_production_kvarh"] = self._aggregate_daily(records, is_power_kw=True)
        except EgdPermissionError:
            _LOGGER.debug("EGD: EAN %s nemá profil IMC1 (jalová dodávka), přeskakuji", ean)
            result["reactive_production_kvarh"] = {}

        _LOGGER.debug(
            "EGD A/B: stažena data %s–%s, spotřeba %d dní, výroba %d dní",
            date_from, date_to,
            len(result["consumption_kwh"]),
            len(result["production_kwh"]),
        )
        return result

    async def _get_daily_data_c1(
        self,
        ean: str,
        date_from: date,
        date_to: date,
    ) -> dict[str, dict[date, float]]:
        """Stáhne a agreguje denní data pro typ C1 (profily DCQC/DSQC)."""
        result: dict[str, dict[date, float]] = {}

        records = await self._fetch_profile(ean, PROFILE_C1_CONSUMPTION, date_from, date_to)
        result["consumption_kwh"] = self._aggregate_daily(records, is_power_kw=False)

        records = await self._fetch_profile(ean, PROFILE_C1_PRODUCTION, date_from, date_to)
        result["production_kwh"] = self._aggregate_daily(records, is_power_kw=False)

        # C1 nemá jalová měření
        result["reactive_consumption_kvarh"] = {}
        result["reactive_production_kvarh"] = {}

        _LOGGER.debug(
            "EGD C1: stažena data %s–%s, spotřeba %d dní, výroba %d dní",
            date_from, date_to,
            len(result["consumption_kwh"]),
            len(result["production_kwh"]),
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

    async def async_get_daily_data(
        self,
        ean: str,
        date_from: date,
        date_to: date,
        meter_type: str = METER_TYPE_AB,
    ) -> dict[str, dict[date, float]]:
        """Stáhne a agreguje denní data dle typu měřiče."""
        if meter_type == METER_TYPE_C1:
            return await self._get_daily_data_c1(ean, date_from, date_to)
        return await self._get_daily_data_ab(ean, date_from, date_to)

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
