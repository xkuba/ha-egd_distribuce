"""
Standalone test EGD OpenAPI – spusť mimo Home Assistant.

Použití:
    cd ha-egd_distribuce/doc
    python3 test_api.py

Přihlašovací údaje načítá z doc/.env (nebo z env proměnných):
    EGD_CLIENT_ID=...
    EGD_CLIENT_SECRET=...
    EGD_EAN=                 # konkrétní EAN, nebo prázdné = testuj všechny z /om
    EGD_TEST_MODE=true       # true = test.distribuce24.cz, false = produkce
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import aiohttp

# ---------------------------------------------------------------------------
# Konfigurace
# ---------------------------------------------------------------------------

def _load_env() -> tuple[str, str, str, bool]:
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    client_id     = os.environ.get("EGD_CLIENT_ID", "")
    client_secret = os.environ.get("EGD_CLIENT_SECRET", "")
    ean           = os.environ.get("EGD_EAN", "").strip()
    test_mode     = os.environ.get("EGD_TEST_MODE", "true").lower() != "false"

    missing = [n for n, v in [("EGD_CLIENT_ID", client_id), ("EGD_CLIENT_SECRET", client_secret)] if not v]
    if missing:
        print(f"CHYBA: chybí proměnné: {', '.join(missing)}")
        sys.exit(1)

    return client_id, client_secret, ean, test_mode


# ---------------------------------------------------------------------------
# API konstanty
# ---------------------------------------------------------------------------

URLS_TEST = {
    "token": "https://test.distribuce24.cz/idm/oauth/token",
    "data":  "https://test.distribuce24.cz/openApi/spotreby",
    "om":    "https://test.distribuce24.cz/openApi/om",
}
URLS_PROD = {
    "token": "https://idm.distribuce24.cz/oauth/token",
    "data":  "https://data.distribuce24.cz/rest/spotreby",
    "om":    "https://data.distribuce24.cz/rest/om",
}
SCOPE = "namerena_data_openapi"

PROFILES = {
    "AB":  {"consumption": "ICC1",  "production": "ISC1"},
    "C1":  {"consumption": "DCQC",  "production": "DSQC"},
}
SUPPORTED_TYPES = {"A", "B", "C1"}


# ---------------------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)

def _ok(msg: str)  -> None: print(f"  ✓  {msg}")
def _err(msg: str) -> None: print(f"  ✗  {msg}")
def _warn(msg: str)-> None: print(f"  ⚠  {msg}")


# ---------------------------------------------------------------------------
# API volání
# ---------------------------------------------------------------------------

async def get_token(session: aiohttp.ClientSession, client_id: str, client_secret: str, urls: dict) -> str | None:
    _section("1. Získání access_token")
    payload = {"grant_type": "client_credentials", "client_id": client_id,
                "client_secret": client_secret, "scope": SCOPE}
    async with session.post(urls["token"], json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        body = await resp.text()
        if resp.status == 200:
            data = json.loads(body)
            token = data.get("access_token", "")
            _ok(f"Token získán (délka: {len(token)} znaků)")
            return token
        _err(f"HTTP {resp.status}: {body}")
        return None


async def get_om_list(session: aiohttp.ClientSession, token: str, urls: dict) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(urls["om"], headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        body = await resp.text()
        if resp.status == 200:
            return json.loads(body)
        _err(f"/om HTTP {resp.status}: {body}")
        return []


async def test_profile(
    session: aiohttp.ClientSession, token: str, ean: str,
    test_date: date, urls: dict, label: str, profile: str,
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    params  = {"ean": ean, "profile": profile,
                "from": f"{test_date.isoformat()}T00:00:00.000",
                "to":   f"{test_date.isoformat()}T23:45:00.000",
                "pageStart": "1", "pageSize": "10"}
    async with session.get(urls["data"], headers=headers, params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as resp:
        body = await resp.text()
        if resp.status == 200:
            data    = json.loads(body)
            records = data[0].get("data", []) if data else []
            statuses = {r.get("status") for r in records}
            _ok(f"{label} (profil {profile}): {len(records)} záznamů, statusy: {statuses}")
            for r in records[:2]:
                print(f"       {r}")
        else:
            _err(f"{label} (profil {profile}): HTTP {resp.status}: {body}")


async def test_ean(
    session: aiohttp.ClientSession, token: str,
    ean: str, typ_mereni: str, test_date: date, urls: dict,
) -> None:
    meter_key = "AB" if typ_mereni.upper() in ("A", "B") else typ_mereni.upper()
    profiles  = PROFILES.get(meter_key)

    print(f"\n  EAN: {ean}  |  Typ měřiče: {typ_mereni}")
    print(f"  {'-' * 50}")

    if not profiles:
        _warn(f"Typ {typ_mereni} není podporován – přeskakuji")
        return

    await test_profile(session, token, ean, test_date, urls, "Spotřeba", profiles["consumption"])
    await test_profile(session, token, ean, test_date, urls, "Dodávka (FVE)", profiles["production"])


# ---------------------------------------------------------------------------
# Hlavní spuštění
# ---------------------------------------------------------------------------

async def main() -> None:
    client_id, client_secret, ean_filter, test_mode = _load_env()

    urls      = URLS_TEST if test_mode else URLS_PROD
    yesterday = date.today() - timedelta(days=1)

    print(f"\nEGD OpenAPI test")
    print(f"Prostředí: {'TESTOVACÍ (test.distribuce24.cz)' if test_mode else 'PRODUKČNÍ (data.distribuce24.cz)'}")
    print(f"EAN:       {ean_filter or '(všechny z /om)'}")
    print(f"Datum:     {yesterday}")

    async with aiohttp.ClientSession() as session:
        token = await get_token(session, client_id, client_secret, urls)
        if not token:
            return

        _section("2. Seznam odběrných míst (/om)")
        om_list = await get_om_list(session, token, urls)
        if not om_list:
            _err("Prázdný seznam – nelze pokračovat")
            return
        _ok(f"Vráceno {len(om_list)} odběrných míst")
        for item in om_list:
            print(f"     {item}")

        # Filtruj dle zadaného EAN, nebo testuj vše
        to_test = [i for i in om_list if not ean_filter or i.get("ean") == ean_filter]
        if ean_filter and not to_test:
            _err(f"EAN {ean_filter} nebyl nalezen v /om")
            return

        _section("3. Test profilů")
        for item in to_test:
            await test_ean(session, token, item["ean"], item.get("typMereni", "?"), yesterday, urls)

    print()


if __name__ == "__main__":
    asyncio.run(main())
