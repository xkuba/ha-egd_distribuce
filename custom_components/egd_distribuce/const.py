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

# Profily typ A/B – spotřeba a výroba (kWh od 1.7.2024, fallback kW ÷ 4)
PROFILE_ICC1 = "ICC1"   # Výkon spotřeby ze sítě – kW
PROFILE_ICQ2 = "ICQ2"   # Spotřeba energie odebrané ze sítě – kWh (preferováno)
PROFILE_ISC1 = "ISC1"   # Výkon dodávky do sítě (FVE přetoky) – kW
PROFILE_ISQ2 = "ISQ2"   # Energie dodávky do sítě – kWh (preferováno)
PROFILE_ISQS = "ISQS"   # Energie dodávky ponížené v rámci sdílení – A, B

# Profily typ A/B – sdílení energie
PROFILE_ICQS = "ICQS"   # Spotřeba energie v rámci sdílení – obchodní – A, B
PROFILE_ICQD = "ICQD"   # Spotřeba energie v rámci sdílení – distribuční – A, B

# Profily typ A/B – jalová energie (preferujeme kWh, fallback kW ÷ 4)
PROFILE_IKC2 = "IKC2"   # Výkon jalové spotřeby při spotřebě – kVAr (preferováno)
PROFILE_IKC1 = "IKC1"   # Výkon jalové spotřeby při spotřebě – kVAr (starší, fallback)
PROFILE_IMQ2 = "IMQ2"   # Energie jalové dodávky při spotřebě – kVArh (preferováno)
PROFILE_IMC1 = "IMC1"   # Výkon jalové dodávky při spotřebě – kVAr (fallback)

# Profily typ C1 (kódy dle /rest/profily)
PROFILE_C1_CONSUMPTION          = "DCQC"   # Spotřeba energie odebrané ze sítě – C1
PROFILE_C1_PRODUCTION           = "DSQC"   # Energie dodávky do sítě (přetok) – C1
PROFILE_C1_SHARING_COMMERCIAL   = "DCQS"   # Spotřeba energie v rámci sdílení – obchodní – C1
PROFILE_C1_SHARING_DISTRIBUTION = "DCQD"   # Spotřeba energie v rámci sdílení – distribuční – C1
PROFILE_C1_PRODUCTION_SHARING   = "DSQS"   # Energie dodávky ponížené v rámci sdílení – C1

# Platný status hodnoty – sjednoceno: W platí pro A/B i C1 (dle dokumentace 2026-05)
STATUS_VALID = "W"

# Config klíče
CONF_CLIENT_ID    = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_EAN          = "ean"

# Options klíče
CONF_UPDATE_HOUR  = "update_hour"    # Hodina stahování dat (0–23)
CONF_HISTORY_FROM = "history_from"   # Datum počátku historie (YYYY-MM-DD)

# Options klíče – tarif HDO a ceny
CONF_HDO_MODE      = "hdo_mode"       # none / smart / classic
CONF_HDO_CODE      = "hdo_code"       # smart kód, např. Cd2526_2
CONF_HDO_PSC       = "hdo_psc"        # PSČ pro určení regionu (klasický kód)
CONF_HDO_A         = "hdo_a"          # klasický příkazový kód – část A
CONF_HDO_B         = "hdo_b"          # klasický příkazový kód – část B
CONF_HDO_DP        = "hdo_dp"         # klasický příkazový kód – část DP
CONF_HDO_VARIANT   = "hdo_variant"    # rozlišení relé, když kód není jednoznačný
CONF_HDO_REFRESH_DAYS = "hdo_refresh_days"  # jak často znovu stahovat rozvrh
CONF_PRICE_PERIODS = "price_periods"  # seznam cenových období (platnost od)

# Režimy určení tarifu
HDO_MODE_NONE    = "none"     # jednotarif – ceny bez rozlišení VT/NT
HDO_MODE_SMART   = "smart"    # smart elektroměr – kód typu Cd2526_2
HDO_MODE_CLASSIC = "classic"  # klasický elektroměr – PSČ + A/B/DP

# Výchozí hodnoty
DEFAULT_SCAN_DAYS   = 30
DEFAULT_UPDATE_HOUR = 17   # Ve 17:xx stahujeme (data jsou dostupná od odpoledne)
# Sezónní přechody zvládne rozvrh z paměti; obnova je kvůli tomu, že distributor
# může změnit samotné časy. To se děje zřídka, týden je rozumný kompromis.
DEFAULT_HDO_REFRESH_DAYS = 7

# Měna nákladových statistik a senzorů
CURRENCY_CZK = "CZK"

# Suffix statistiky nákladů (doplňuje se k profilu spotřeby)
STAT_SUFFIX_COST = "consumption_cost"

# Coordinator klíče
COORDINATOR_KEY = "coordinator"

# Statistic ID prefix pro recorder
STATISTIC_ID_PREFIX = f"sensor.{DOMAIN}"
