"""Konstanty pro EG.D Distribuce integraci."""

DOMAIN = "egd_distribuce"

# API endpoints
API_TOKEN_URL = "https://idm.distribuce24.cz/oauth/token"
API_DATA_URL = "https://data.distribuce24.cz/rest/spotreby"
API_PROFILES_URL = "https://data.distribuce24.cz/rest/profily"
API_SCOPE = "namerena_data_openapi"

# Profily – preferujeme kWh (od 1.7.2024), fallback na kW ÷ 4
PROFILE_ICC1 = "ICC1"   # Spotřeba ze sítě – výkon kW
PROFILE_ICQ2 = "ICQ2"   # Spotřeba ze sítě – energie kWh (od 1.7.2024)
PROFILE_ISC1 = "ISC1"   # Dodávka do sítě (FVE přetoky) – výkon kW
PROFILE_ISQ2 = "ISQ2"   # Dodávka do sítě – energie kWh (od 1.7.2024)
PROFILE_IKC1 = "IKC1"   # Jalová spotřeba při spotřebě – kVAr
PROFILE_IKQ1 = "IKQ1"   # Jalová spotřeba – energie kVArh
PROFILE_IMC1 = "IMC1"   # Jalová dodávka při spotřebě – kVAr
PROFILE_IMQ2 = "IMQ2"   # Jalová dodávka – energie kVArh

# Stavové kódy hodnot
STATUS_VALID = "IU012"   # Hodnota je platná

# Config klíče
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_EAN = "ean"
CONF_SCAN_DAYS = "scan_days"   # Kolik dní zpětně stahovat při prvním spuštění

# Výchozí hodnoty
DEFAULT_SCAN_DAYS = 30
DEFAULT_UPDATE_HOUR = 17   # Ve 17:xx stahujeme (data jsou dostupná od odpoledne)

# Coordinator klíče
COORDINATOR_KEY = "coordinator"

# Statistic ID prefix pro recorder
STATISTIC_ID_PREFIX = f"sensor.{DOMAIN}"
