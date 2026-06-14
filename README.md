# EG.D Distribuce – Home Assistant integrace

> ⚠️ **Stav vývoje:** Integrace byla vyvinuta a testována výhradně na **testovacím prostředí** EGD (test.distribuce24.cz) s testovacími EAN. Na reálných odběrných místech **nebylo ověřeno, že vrácené hodnoty odpovídají datům na portálu Distribuce24.** Používejte na vlastní riziko a případné nesrovnalosti hlaste jako issue.

Custom integrace pro stahování naměřených dat z EG.D Distribuce přes OpenAPI portálu Distribuce24.

## Co integrace umí

### Spotřeba a výroba
- ✅ Denní spotřeba ze sítě (kWh)
- ✅ Denní dodávka do sítě – FVE přetoky (kWh)
- ✅ Jalová spotřeba a dodávka (kVArh) – pouze typ A/B

### Sdílení energie (komunitní FVE)
- ✅ Sdílení energie – obchodní část (kWh)
- ✅ Sdílení energie – distribuční část (kWh)
- ✅ Dodávka ponížená v rámci sdílení (kWh)

### Integrace do HA
- ✅ Nastavení přes GUI (Config Flow) – žádný YAML
- ✅ Automatická detekce typu měřiče (A, B, C1) z API
- ✅ Automatická detekce EAN bez výrobny (C1: mirror detection DSQC vs DCQC)
- ✅ Zařízení (Device) v HA UI – vše pohromadě pod jedním EAN
- ✅ Správné historické timestampy – data se zapíší do správného dne, ne dne stažení
- ✅ Napojení na **Energy Dashboard** (Přehled energií)
- ✅ Automatický fallback ICC1 → ICQ2 (kWh od 1.7.2024) pro typ A/B
- ✅ Smart history sync – při restartu HA se stahují jen chybějící dny, ne celá historie
- ✅ Počáteční synchronizace konfigurovatelné zpětné historie při prvním spuštění
- ✅ Volitelné testovací prostředí (test.distribuce24.cz)

## Senzory

| Senzor | Typ A/B | Typ C1 | Profil API |
|---|:---:|:---:|---|
| Spotřeba ze sítě | ✓ | ✓ | ICQ2 / ICC1 / DCQC |
| Dodávka do sítě (FVE) | ✓ | ✓ | ISQ2 / ISC1 / DSQC |
| Sdílení energie – obchodní | ✓ | ✓ | ICQS / DCQS |
| Sdílení energie – distribuční | ✓ | ✓ | ICQD / DCQD |
| Dodávka ponížená sdílením | ✓ | ✓ | ISQS / DSQS |
| Jalová spotřeba | ✓ | – | IKC2 / IKC1 |
| Jalová dodávka | ✓ | – | IMQ2 / IMC1 |

Senzory sdílení a dodávky ponížené sdílením se zobrazí jen pokud API pro daný EAN tato data poskytuje (EAN je zapojen do skupiny sdílení energie).

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
| Datum počátku historie | 30 dní zpět | Od tohoto data se stáhne zpětná historie při prvním spuštění |

## Napojení na Energy Dashboard

Integrace zapisuje data jako **external statistics** – ty nejsou automaticky na dashboardu, je třeba je přidat ručně.

1. **Nastavení → Přehled energií → Spotřeba elektřiny ze sítě → Přidat spotřebu**
2. Zadej `statistic_id` (viditelné jako atribut senzoru nebo v Vývojářské nástroje → Statistiky):

| Měřená veličina | statistic_id |
|---|---|
| Spotřeba ze sítě | `egd_distribuce:<EAN>_consumption` |
| Dodávka do sítě (FVE) | `egd_distribuce:<EAN>_production` |
| Sdílení – obchodní | `egd_distribuce:<EAN>_sharing_commercial` |
| Sdílení – distribuční | `egd_distribuce:<EAN>_sharing_distribution` |
| Dodávka ponížená sdílením | `egd_distribuce:<EAN>_production_sharing` |

Pro zobrazení historického grafu v Lovelace (statistics-graph karta):
```yaml
type: statistics-graph
entities:
  - egd_distribuce:<EAN>_consumption
period: day
stat_types:
  - sum
```

## Smazání a opětovné stažení dat

Pokud potřebuješ data vymazat a stáhnout znovu:
1. **Vývojářské nástroje → Statistiky** → smaž záznamy s prefixem `egd_distribuce`
2. **Nastavení → Zařízení a služby → EG.D Distribuce → ⋮ → Znovu načíst**

## Technické poznámky

- Data jsou aktualizována jednou denně (výchozí: ~17:00, konfigurovatelné)
- Smart sync: při každém startu HA se stáhnou jen dny, které v recorderu chybí – nedochází k přepisování existujících dat
- Token je platný do půlnoci – integrace ho automaticky obnovuje každý den
- Platný status hodnoty je `W` pro všechny typy měřičů (dle API dokumentace 2026-05)
- Hranice API `/rest/spotreby` je exkluzivní pro `from` – integrace to kompenzuje automaticky

## Dokumentace

- [EG.D OpenAPI – Uživatelský návod (PDF)](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf)

## Poděkování

Integrace byla vyvinuta ve spolupráci s [Claude](https://claude.ai) (Anthropic) – AI asistent se podílel na návrhu architektury, implementaci API klienta, ladění a dokumentaci.
