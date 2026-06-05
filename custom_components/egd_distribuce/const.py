"""Konstanty pro EG.D Distribuce integraci."""

DOMAIN = "egd_distribuce"

# API endpoints – produkce
API_TOKEN_URL = "https://idm.distribuce24.cz/oauth/token"
API_DATA_URL  = "https://data.distribuce24.cz/rest/spotreby"
API_OM_URL    = "https://data.distribuce24.cz/rest/om"

# API endpoints – testovací prostředí
API_TOKEN_URL_TEST = "https://test.distribuce24.cz/idm/oauth/token"
API_DATA_URL_TEST  = "https://test.distribuce24.cz/openApi/spotreby"
API_OM_URL_TEST    = "https://test.distribuce24.cz/openApi/om"

API_SCOPE = "namerena_data_openapi"

# Config klíč pro přepínač testovacího prostředí
CONF_TEST_MODE = "test_mode"

# Typ měřiče (A a B jsou aliasy pro AB)
METER_TYPE_AB = "AB"
METER_TYPE_C1 = "C1"
CONF_METER_TYPE = "meter_type"

# Profily typ A/B – preferujeme kWh (od 1.7.2024), fallback na kW ÷ 4
PROFILE_ICC1 = "ICC1"   # Spotřeba ze sítě – výkon kW
PROFILE_ICQ2 = "ICQ2"   # Spotřeba ze sítě – energie kWh (od 1.7.2024)
PROFILE_ISC1 = "ISC1"   # Dodávka do sítě (FVE přetoky) – výkon kW
PROFILE_ISQ2 = "ISQ2"   # Dodávka do sítě – energie kWh (od 1.7.2024)
PROFILE_IKC1 = "IKC1"   # Jalová spotřeba – výkon kVAr
PROFILE_IMC1 = "IMC1"   # Jalová dodávka – výkon kVAr

# Profily typ C1 (nové kódy dle dokumentace 2026-05)
PROFILE_C1_CONSUMPTION = "DCQC"   # Spotřeba energie ze sítě – C1
PROFILE_C1_PRODUCTION  = "DSQC"   # Dodávka energie do sítě – C1

# Platný status hodnoty – sjednoceno: W platí pro A/B i C1 (dle dokumentace 2026-05)
STATUS_VALID = "W"

# Config klíče
CONF_CLIENT_ID    = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_EAN          = "ean"

# Options klíče
CONF_UPDATE_HOUR  = "update_hour"    # Hodina stahování dat (0–23)
CONF_HISTORY_FROM = "history_from"   # Datum počátku historie (YYYY-MM-DD)

# Výchozí hodnoty
DEFAULT_SCAN_DAYS   = 30
DEFAULT_UPDATE_HOUR = 17   # Ve 17:xx stahujeme (data jsou dostupná od odpoledne)

# Coordinator klíče
COORDINATOR_KEY = "coordinator"

# Statistic ID prefix pro recorder
STATISTIC_ID_PREFIX = f"sensor.{DOMAIN}"
