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
    test_date_str = os.environ.get("EGD_TEST_DATE", "").strip()

    missing = [n for n, v in [("EGD_CLIENT_ID", client_id), ("EGD_CLIENT_SECRET", client_secret)] if not v]
    if missing:
        print(f"CHYBA: chybí proměnné: {', '.join(missing)}")
        sys.exit(1)

    return client_id, client_secret, ean, test_mode, test_date_str


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

# Profily pro A/B: (label, kód, is_power_kw)
# is_power_kw=True  → API vrací kW, přepočet ÷ 4 na kWh za 15 min
# is_power_kw=False → API vrací přímo kWh
PROFILES_AB = [
    ("Spotřeba ze sítě (ICQ2/kWh)",        "ICQ2", False),
    ("Spotřeba ze sítě – fallback (ICC1/kW)", "ICC1", True),
    ("Dodávka do sítě (ISQ2/kWh)",         "ISQ2", False),
    ("Sdílení – obchodní (ICQS)",          "ICQS", False),
    ("Sdílení – distribuční (ICQD)",       "ICQD", False),
    ("Dodávka ponížená sdílením (ISQS)",   "ISQS", False),
    ("Jalová spotřeba (IKC2/kW)",          "IKC2", True),
    ("Jalová dodávka (IMQ2/kWh)",          "IMQ2", False),
]

PROFILES = {
    "AB": {
        "consumption": "ICC1",
        "production":  "ISC1",
    },
    "C1": {
        "consumption":           "DCQC",
        "production":            "DSQC",
        "sharing_commercial":    "DCQS",
        "sharing_distribution":  "DCQD",
    },
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
    is_power_kw: bool = False,
) -> list[dict] | None:
    """Vrátí záznamy při úspěchu, None při chybě."""
    headers = {"Authorization": f"Bearer {token}"}
    # Exkluzivní dolní mez: from = předchozí den 23:45, aby byl zahrnut interval 00:00
    prev_day = test_date - timedelta(days=1)
    params  = {"ean": ean, "profile": profile,
                "from": f"{prev_day.isoformat()}T23:45:00.000",
                "to":   f"{test_date.isoformat()}T23:59:00.000",
                "pageStart": "1", "pageSize": "200"}
    async with session.get(urls["data"], headers=headers, params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as resp:
        body = await resp.text()
        if resp.status == 200:
            data    = json.loads(body)
            records = data[0].get("data", []) if data else []
            statuses = {r.get("status") for r in records}
            valid   = [r for r in records if r.get("status") == "W"]
            total   = sum(float(r["value"]) / 4.0 if is_power_kw else float(r["value"])
                          for r in valid if r.get("value") is not None)
            unit    = "kWh" if not is_power_kw else "kWh (z kW÷4)"
            _ok(f"{label} (profil {profile}): {len(records)} zázn, sum={total:.4f} {unit}, statusy: {statuses}")
            for r in records[:2]:
                print(f"       {r}")
            return records
        else:
            _err(f"{label} (profil {profile}): HTTP {resp.status}: {body[:200]}")
            return None


def _records_are_identical(a: list[dict], b: list[dict]) -> bool:
    if len(a) != len(b):
        return False
    return all(
        x.get("timestamp") == y.get("timestamp") and x.get("value") == y.get("value")
        for x, y in zip(a, b)
    )


async def test_ean(
    session: aiohttp.ClientSession, token: str,
    ean: str, typ_mereni: str, test_date: date, urls: dict,
) -> None:
    meter_key = "AB" if typ_mereni.upper() in ("A", "B") else typ_mereni.upper()

    print(f"\n  EAN: {ean}  |  Typ měřiče: {typ_mereni}")
    print(f"  {'-' * 50}")

    if meter_key not in SUPPORTED_TYPES and meter_key != "AB":
        _warn(f"Typ {typ_mereni} není podporován – přeskakuji")
        return

    if meter_key == "AB":
        # Test všech A/B profilů s výpisem součtu
        records_by_profile: dict[str, list[dict]] = {}
        for label, profile, is_kw in PROFILES_AB:
            records = await test_profile(session, token, ean, test_date, urls, label, profile, is_kw)
            if records is not None:
                records_by_profile[profile] = records

        # Mirror detection: ISQ2 vs ICQ2
        icq2 = records_by_profile.get("ICQ2", [])
        isq2 = records_by_profile.get("ISQ2", [])
        if icq2 and isq2:
            if _records_are_identical(icq2, isq2):
                _warn("DETEKCE ZRCADLENÍ: ISQ2 == ICQ2 → EAN NEMÁ výrobu (API zrcadlí spotřebu)")
            else:
                _ok("ISQ2 ≠ ICQ2 → EAN MÁ výrobu (data se liší)")
        elif icq2 and not isq2:
            _ok("ISQ2 vrátil chybu (HTTP 400) → EAN NEMÁ výrobu")

    else:
        # C1 – původní profily
        profiles = PROFILES.get(meter_key, {})
        dcqc_records = None
        for label, key in [
            ("Spotřeba ze sítě",              "consumption"),
            ("Dodávka do sítě (FVE/přetok)",  "production"),
            ("Sdílení – obchodní (DCQS)",      "sharing_commercial"),
            ("Sdílení – distribuční (DCQD)",   "sharing_distribution"),
        ]:
            if key in profiles:
                recs = await test_profile(session, token, ean, test_date, urls, label, profiles[key])
                if key == "consumption":
                    dcqc_records = recs
                if key == "production" and dcqc_records and recs:
                    if _records_are_identical(dcqc_records, recs):
                        _warn("DETEKCE ZRCADLENÍ: DSQC == DCQC → EAN NEMÁ výrobu")
                    else:
                        _ok("DSQC ≠ DCQC → EAN MÁ výrobu")


# ---------------------------------------------------------------------------
# Hlavní spuštění
# ---------------------------------------------------------------------------

async def main() -> None:
    client_id, client_secret, ean_filter, test_mode, test_date_str = _load_env()

    urls = URLS_TEST if test_mode else URLS_PROD

    if test_date_str:
        try:
            test_date = date.fromisoformat(test_date_str)
        except ValueError:
            print(f"CHYBA: neplatné datum EGD_TEST_DATE='{test_date_str}', použij formát YYYY-MM-DD")
            sys.exit(1)
    else:
        test_date = date.today() - timedelta(days=1)

    print(f"\nEGD OpenAPI test")
    print(f"Prostředí: {'TESTOVACÍ (test.distribuce24.cz)' if test_mode else 'PRODUKČNÍ (data.distribuce24.cz)'}")
    print(f"EAN:       {ean_filter or '(všechny z /om)'}")
    print(f"Datum:     {test_date}")

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
            await test_ean(session, token, item["ean"], item.get("typMereni", "?"), test_date, urls)

    print()


if __name__ == "__main__":
    asyncio.run(main())
