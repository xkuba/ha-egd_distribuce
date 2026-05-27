"""EG.D Distribuce API klient."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    API_DATA_URL,
    API_TOKEN_URL,
    API_SCOPE,
    PROFILE_ICC1,
    PROFILE_ICQ2,
    PROFILE_IMC1,
    PROFILE_IMQ2,
    PROFILE_IKC1,
    PROFILE_IKQ1,
    PROFILE_ISC1,
    PROFILE_ISQ2,
    STATUS_VALID,
)

_LOGGER = logging.getLogger(__name__)

# Datum zavedení kWh profilů
KWH_PROFILES_SINCE = date(2024, 7, 1)


class EgdApiError(Exception):
    """Obecná chyba API."""


class EgdAuthError(EgdApiError):
    """Chyba autentizace (401)."""


class EgdPermissionError(EgdApiError):
    """Oprávnění pro EAN nebo období odmítnuto (400 validation_error)."""


class EgdApi:
    """Klient pro EG.D Distribuce OpenAPI."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_date: date | None = None

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
                API_TOKEN_URL,
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
    # Načtení dat pro jeden profil
    # ------------------------------------------------------------------

    async def _fetch_profile(
        self,
        ean: str,
        profile: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Stáhne čtvrthodinová data pro daný profil a rozsah dat."""
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}

        # API interpretuje časy v lokálním čase (CET/CEST), ne UTC
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

        try:
            async with self._session.get(
                API_DATA_URL,
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

        Pro kW profily (ICC1, ISC1...): hodnota kW ÷ 4 = kWh za čtvrthodinu,
        součet = denní kWh.
        Pro kWh profily (ICQ2, ISQ2...): hodnoty jsou již energie, jen sečteme.
        """
        daily: dict[date, float] = {}

        for rec in records:
            # Přeskočíme neplatné hodnoty
            if rec.get("status") != STATUS_VALID:
                continue

            value = rec.get("value")
            if value is None:
                continue

            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            # Timestamp je v UTC – převedeme na datum
            ts_str = rec.get("timestamp", "")
            try:
                dt_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                # Lokální datum CZ (UTC+1/UTC+2) – pro přiřazení ke dni
                dt_local = dt_utc.astimezone()
                day = dt_local.date()
            except (ValueError, TypeError):
                continue

            # Přepočet kW → kWh pro výkonové profily
            energy = value / 4.0 if is_power_kw else value

            daily[day] = daily.get(day, 0.0) + energy

        return daily

    # ------------------------------------------------------------------
    # Veřejné metody
    # ------------------------------------------------------------------

    async def async_validate_credentials(self, ean: str) -> bool:
        """Ověří přihlašovací údaje pokusem o získání tokenu a dat za 1 den."""
        await self._ensure_token()
        yesterday = date.today() - timedelta(days=1)
        # Zkusíme nejjednodušší profil
        await self._fetch_profile(ean, PROFILE_ICC1, yesterday, yesterday)
        return True

    async def async_get_daily_data(
        self,
        ean: str,
        date_from: date,
        date_to: date,
    ) -> dict[str, dict[date, float]]:
        """
        Stáhne a agreguje denní data pro všechny profily.

        Vrací slovník: { "consumption_kwh": {date: float}, ... }
        Automaticky volí kWh profily pokud je datum >= 1.7.2024,
        jinak kW profily s přepočtem ÷4.
        """
        use_kwh = date_from >= KWH_PROFILES_SINCE

        result: dict[str, dict[date, float]] = {}

        # --- Spotřeba ---
        consumption_profile = PROFILE_ICQ2 if use_kwh else PROFILE_ICC1
        records = await self._fetch_profile(ean, consumption_profile, date_from, date_to)
        if not records and use_kwh:
            # Fallback na kW profil pokud kWh ještě není k dispozici
            _LOGGER.debug("EGD: ICQ2 prázdné, fallback na ICC1")
            records = await self._fetch_profile(ean, PROFILE_ICC1, date_from, date_to)
            result["consumption_kwh"] = self._aggregate_daily(records, is_power_kw=True)
        else:
            result["consumption_kwh"] = self._aggregate_daily(
                records, is_power_kw=not use_kwh
            )

        # --- Výroba / FVE přetoky ---
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

        # --- Jalová spotřeba ---
        records = await self._fetch_profile(ean, PROFILE_IKC1, date_from, date_to)
        result["reactive_consumption_kvarh"] = self._aggregate_daily(
            records, is_power_kw=True
        )

        # --- Jalová dodávka ---
        records = await self._fetch_profile(ean, PROFILE_IMC1, date_from, date_to)
        result["reactive_production_kvarh"] = self._aggregate_daily(
            records, is_power_kw=True
        )

        _LOGGER.debug(
            "EGD: stažena data %s–%s, spotřeba %d dní, výroba %d dní",
            date_from,
            date_to,
            len(result["consumption_kwh"]),
            len(result["production_kwh"]),
        )
        return result
