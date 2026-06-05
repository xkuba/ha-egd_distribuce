# EG.D Distribuce – Home Assistant integrace

> ⚠️ **Stav vývoje:** Integrace byla vyvinuta a testována výhradně na **testovacím prostředí** EGD (test.distribuce24.cz) s testovacími EAN. Na reálných odběrných místech **nebylo ověřeno, že vrácené hodnoty odpovídají datům na portálu Distribuce24.** Používejte na vlastní riziko a případné nesrovnalosti hlaste jako issue.

Custom integrace pro stahování naměřených dat z EG.D Distribuce přes OpenAPI portálu Distribuce24.

## Co integrace umí

- ✅ Denní spotřeba ze sítě (kWh)
- ✅ Denní dodávka do sítě – FVE přetoky (kWh)
- ✅ Jalová spotřeba a dodávka (kVArh) – pouze typ A/B
- ✅ Nastavení přes GUI (Config Flow) – žádný YAML
- ✅ Automatická detekce typu měřiče (A, B, C1) z API
- ✅ Zařízení (Device) v HA UI – vše pohromadě pod jedním EAN
- ✅ Správné historické timestampy – data se zapíší do správného dne, ne dne stažení
- ✅ Napojení na **Energy Dashboard** (Přehled energií)
- ✅ Automatický fallback ICC1/ISC1 → ICQ2/ISQ2 (kWh od 1.7.2024) pro typ A/B
- ✅ Počáteční synchronizace konfigurovatelné zpětné historie při prvním spuštění
- ✅ Volitelné testovací prostředí (test.distribuce24.cz)

## Požadavky

- Odběrné místo s typem měření **A, B nebo C1** (průmysl, výrobny, FVE, chytré elektroměry)
- ⚠️ **Typ C4** (domácnosti bez chytrého elektroměru) **není podporován** – EGD API pro tento typ neposkytuje data
- Účet na portálu [portal.distribuce24.cz](https://portal.distribuce24.cz)
- Vygenerovaný `client_id` a `client_secret` (Správa účtů → Vzdálený přístup – OPENAPI)

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
   - **Client ID** – z portálu Distribuce24
   - **Client Secret** – tamtéž
   - **EAN** – EAN číslo odběrného místa (18 číslic)
   - **Testovací prostředí** – zaškrtni pouze při testování s testovacími přihlašovacími údaji
3. Typ měřiče se zjistí automaticky z API – není třeba ho zadávat ručně
4. Integrace stáhne historii dle nastavení (výchozí: posledních 30 dní)

Pro každé odběrné místo přidej integraci zvlášť.

## Nastavení (options)

Po přidání integrace lze upravit v **Nastavení → Zařízení a služby → EG.D Distribuce → Konfigurovat**:

| Parametr | Výchozí | Popis |
|---|---|---|
| Hodina stahování | 17 | Data se stáhnou při prvním ticku v nebo po zadané hodině |
| Datum počátku historie | 30 dní zpět | Od tohoto data se při příštím startu stáhne celá historie |

## Napojení na Energy Dashboard

1. **Nastavení → Přehled energií → Spotřeba elektřiny ze sítě**
2. Vyber senzor `EG.D Distribuce {EAN} Spotřeba ze sítě`
3. Pro FVE: **Zpětné přetoky do sítě** → `EG.D Distribuce {EAN} Dodávka do sítě (FVE)`

## Technické poznámky

- Data jsou aktualizována jednou denně (výchozí: ~17:03, konfigurovatelné)
- Token je platný do půlnoci – integrace ho automaticky obnovuje každý den
- Max. 3000 záznamů na volání = cca 1 měsíc čtvrthodinových dat
- Data se zapisují přes `recorder.import_statistics` se správným timestampem
- Platný status hodnoty je `W` pro všechny typy měřičů (dle API dokumentace 2026-05)

## Dokumentace

- [EG.D OpenAPI – Uživatelský návod (PDF)](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf)

## Poděkování

Integrace byla vyvinuta ve spolupráci s [Claude](https://claude.ai) (Anthropic) – AI asistent se podílel na návrhu architektury, implementaci API klienta, ladění a dokumentaci.
