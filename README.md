# EG.D Distribuce – Home Assistant integrace

Custom integrace pro stahování naměřených dat z EG.D Distribuce přes OpenAPI portálu Distribuce24.

## Co integrace umí

- ✅ Denní spotřeba ze sítě (kWh)
- ✅ Denní dodávka do sítě – FVE přetoky (kWh)
- ✅ Jalová spotřeba a dodávka (kVArh)
- ✅ Nastavení přes GUI (Config Flow) – žádný YAML
- ✅ Zařízení (Device) v HA UI – vše pohromadě pod jedním EAN
- ✅ Správné historické timestampy – data se zapíší do správného dne, ne dne stažení
- ✅ Napojení na **Energy Dashboard** (Přehled energií)
- ✅ Automatický fallback ICC1/ISC1 → ICQ2/ISQ2 (kWh od 1.7.2024)
- ✅ Počáteční synchronizace 30 dní zpětné historie při prvním spuštění

## Požadavky

- Odběrné místo s **typem měření A nebo B** (FVE, výrobny, velkoodběr, nepřímé měření)
- Účet na portálu [portal.distribuce24.cz](https://portal.distribuce24.cz)
- Vygenerovaný `client_id` a `client_secret`

## Instalace

### Přes HACS (doporučeno)
1. HACS → Custom repositories → přidej URL tohoto repozitáře → kategorie Integration
2. HACS → Integrace → EG.D Distribuce → Instalovat
3. Restart HA

### Ručně
1. Zkopíruj složku `custom_components/egd_distribuce` do `<config>/custom_components/`
2. Restart HA

## Nastavení

1. **Nastavení → Zařízení a služby → Přidat integraci → EG.D Distribuce**
2. Zadej:
   - **Client ID** – z portálu Distribuce24 → Správa účtů → Vzdálený přístup – OPENAPI
   - **Client Secret** – tamtéž
   - **EAN** – EAN číslo odběrného místa (18 číslic, zadej spotřební EAN)
3. Integrace stáhne posledních 30 dní historie

## Napojení na Energy Dashboard

1. **Nastavení → Přehled energií → Spotřeba elektřiny ze sítě**
2. Vyber senzor `EG.D Distribuce {EAN} Spotřeba ze sítě`
3. Pro FVE: **Zpětné přetoky do sítě** → `EG.D Distribuce {EAN} Dodávka do sítě (FVE)`

## Technické poznámky

- Data jsou aktualizována jednou denně odpoledne (~17:00)
- Token je platný do půlnoci – integrace ho automaticky obnovuje každý den
- Max. 3000 záznamů na volání = cca 1 měsíc čtvrthodinových dat
- Data se zapisují přes `recorder.import_statistics` se správným timestampem
