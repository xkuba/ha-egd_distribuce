# EG.D Distribuce – Home Assistant integrace

Custom integrace pro stahování naměřených dat z EG.D Distribuce přes OpenAPI portálu Distribuce24.

> **Stav ověření:** Typ měření **C1** je ověřen proti produkčnímu API – hodinové i denní součty souhlasí s hodnotami na portálu Distribuce24. Typy **A/B** jsou implementovány dle dokumentace, ale na reálném odběrném místě zatím ověřeny nebyly. Nesrovnalosti prosím hlaste jako issue.

## Co integrace umí

### Spotřeba a výroba
- ✅ Spotřeba ze sítě (kWh)
- ✅ Dodávka do sítě – FVE přetoky (kWh)
- ✅ Jalová spotřeba a dodávka (kVArh) – pouze typ A/B

### Sdílení energie (komunitní FVE)
- ✅ Sdílení energie – obchodní část (kWh)
- ✅ Sdílení energie – distribuční část (kWh)
- ✅ Dodávka ponížená v rámci sdílení (kWh)

### Integrace do HA
- ✅ Nastavení přes GUI (Config Flow) – žádný YAML
- ✅ **Hodinová granularita** statistik – Energy Dashboard ukáže skutečný průběh dne
- ✅ Automatická detekce typu měřiče (A, B, C1) z API
- ✅ Automatické zakázání senzorů, pro které odběrné místo nemá data
- ✅ Správné historické timestampy – data se zapíší do správného dne a hodiny, ne dne stažení
- ✅ Smart sync – při restartu HA se stahují jen chybějící hodiny, ne celá historie
- ✅ Automatické zúžení rozsahu na období, na které má účet oprávnění
- ✅ Stránkování odpovědí API – funguje i pro historii delší než měsíc
- ✅ Volitelné testovací prostředí (test.distribuce24.cz)

## Jak to funguje – statistiky vs. senzory

Tohle je nejdůležitější část k pochopení. Integrace zapisuje data do **dvou různých míst** v Home Assistantu a každé slouží k něčemu jinému.

| | Statistika (external statistics) | Senzor (entita) |
|---|---|---|
| **Co obsahuje** | kompletní historii po hodinách | jednu hodnotu: součet za poslední dostupný den |
| **Identifikátor** | `egd_distribuce:<EAN>_consumption` | `sensor.<...>_spotreba_ze_site` |
| **Zpětný zápis** | ✅ ano – proto tu je historie | ❌ ne (viz níže) |
| **K čemu použít** | Energy Dashboard, grafy historie | rychlý přehled „kolik bylo včera" |

### Proč historie patří do statistik

Data z EG.D chodí **zpětně** – včerejší spotřeba se stahuje dnes odpoledne. Home Assistant má pro takový případ mechanismus *external statistics*, který umí zapsat hodnotu s libovolným historickým časovým razítkem. Integrace tedy načte čtvrthodinové záznamy z API, sečte je po hodinách a zapíše je pod čas, kdy energie skutečně protekla – ne pod čas stažení.

### Proč senzor nemá historii

Historie entity (tabulka `states`) se v HA plní **jen skutečnými změnami stavu v reálném čase**. Neexistuje API, kterým by šel vložit minulý stav. Senzor se proto v grafu History změní vždy jen jednou denně, v okamžik stažení dat – a je to tak správně, není to chyba.

Senzory záměrně **nemají `state_class`**. Kdyby ho měly, HA by si z jejich jednou denně se měnící hodnoty počítal vlastní statistiky – a protože denní spotřeba mezi dny klesá i roste, vyhodnocoval by poklesy jako reset měřidla a do Energy Dashboardu by se dostala nesmyslná čísla.

**Shrnutí:** do Energy Dashboardu a do grafů vždy vybírejte **statistiku** `egd_distribuce:…`, nikoli entitu senzoru.

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

Senzory profilů, pro které API u daného EAN nevrací žádná data (typicky výroba a sdílení u běžné domácnosti), se založí jako **zakázané**. V seznamu entit je uvidíte šedivé a můžete je kdykoli povolit ručně. Pokud se data později objeví (např. po instalaci FVE), integrace je povolí sama.

## Požadavky

- Odběrné místo s typem měření **A, B nebo C1** (průmysl, výrobny, FVE, chytré elektroměry)
- ⚠️ **Typ C4** (domácnosti bez chytrého elektroměru) **není podporován** – EGD API pro tento typ neposkytuje data
- Účet na portálu [portal.distribuce24.cz](https://portal.distribuce24.cz)
- Vygenerovaný `client_id` a `client_secret` (Správa účtů → Vzdálený přístup – OPENAPI)

## Instalace

### Přes HACS
1. HACS → Custom repositories → přidej URL tohoto repozitáře → kategorie Integration
2. HACS → Integrace → EG.D Distribuce → Instalovat
3. Restart HA

### Ručně
1. Zkopíruj **celou** složku `custom_components/egd_distribuce` do `<config>/custom_components/`
2. Restart HA

> Při ruční aktualizaci vždy kopíruj všechny soubory najednou. Částečný přenos (nový `api.py` se starým `__init__.py`) skončí chybou při startu integrace.

## Nastavení

1. **Nastavení → Zařízení a služby → Přidat integraci → EG.D Distribuce**
2. Zadej:
   - **Client ID** – z portálu Distribuce24
   - **Client Secret** – tamtéž
   - **EAN** – EAN číslo odběrného místa (18 číslic)
   - **Testovací prostředí** – zaškrtni pouze při testování s testovacími přihlašovacími údaji
   - **Datum načítání historie od** – volitelné, prázdné = posledních 30 dní
3. Typ měřiče se zjistí automaticky z API – není třeba ho zadávat ručně

Pro každé odběrné místo přidej integraci zvlášť.

### Options

Po přidání lze upravit v **Nastavení → Zařízení a služby → EG.D Distribuce → Konfigurovat**:

| Parametr | Výchozí | Popis |
|---|---|---|
| Hodina stahování | 17 | Data se stáhnou při prvním ticku v nebo po zadané hodině |
| Datum počátku historie | 30 dní zpět | Od tohoto data se stáhne zpětná historie |

Pokud zadané datum sahá dál, než kam má účet oprávnění, integrace rozsah **sama zúží** na nejstarší dostupný den – nespadne.

## Napojení na Energy Dashboard

Integrace zapisuje data jako external statistics. Ty se na dashboard nepřidají samy.

1. **Nastavení → Přehled energií → Spotřeba elektřiny ze sítě → Přidat spotřebu**
2. Vyber statistiku podle názvu **`EGD <EAN> Spotřeba ze sítě`**, případně zadej `statistic_id`:

| Měřená veličina | statistic_id |
|---|---|
| Spotřeba ze sítě | `egd_distribuce:<EAN>_consumption` |
| Dodávka do sítě (FVE) | `egd_distribuce:<EAN>_production` |
| Sdílení – obchodní | `egd_distribuce:<EAN>_sharing_commercial` |
| Sdílení – distribuční | `egd_distribuce:<EAN>_sharing_distribution` |
| Dodávka ponížená sdílením | `egd_distribuce:<EAN>_production_sharing` |
| Jalová spotřeba | `egd_distribuce:<EAN>_reactive_consumption` |
| Jalová dodávka | `egd_distribuce:<EAN>_reactive_production` |

⚠️ Nevybírejte entitu senzoru (`sensor.…`) – ta do Energy Dashboardu nepatří, viz [Jak to funguje](#jak-to-funguje--statistiky-vs-senzory).

### Graf historie v Lovelace

Karta History umí zobrazit jen stavy entit, takže na historická data **nestačí**. Použij kartu **Statistics graph**.

**Průběh dne po hodinách** – každý sloupec je spotřeba za jednu hodinu. Odpovídá tomu, co ukazuje Energy Dashboard:

```yaml
type: statistics-graph
entities:
  - egd_distribuce:<EAN>_consumption
period: hour
chart_type: bar
stat_types:
  - state
```

**Denní součty** – každý sloupec je spotřeba za celý den:

```yaml
type: statistics-graph
entities:
  - egd_distribuce:<EAN>_consumption
period: day
chart_type: bar
stat_types:
  - change
```

#### Který `stat_type` zvolit

| `stat_type` | Co vykreslí |
|---|---|
| `state` | hodnotu uloženou v jednom záznamu – při `period: hour` je to spotřeba dané hodiny |
| `change` | přírůstek kumulativního součtu za periodu – správná volba pro denní, týdenní a měsíční součty |
| `sum` | kumulativní součet od začátku měření – stále rostoucí křivka, zřídka to, co chcete |

⚠️ Při `period: day` (a delších) **nepoužívejte `state`**. Agregace na delší periodu přebírá hodnotu posledního záznamu v periodě, takže byste místo celodenní spotřeby dostali jen spotřebu poslední hodiny dne. Pro součty za den slouží `change`.

## Smazání a opětovné stažení dat

1. **Vývojářské nástroje → Statistiky** → smaž záznamy s prefixem `egd_distribuce`
2. **Nastavení → Zařízení a služby → EG.D Distribuce → ⋮ → Znovu načíst**

Smazání statistik je nutné i při přechodu z verze, která zapisovala data v jiné granularitě – jinak se nové hodinové záznamy navěsí na staré a graf bude nekonzistentní.

## Technické poznámky

- Data se aktualizují jednou denně (výchozí ~17:00, konfigurovatelné). Coordinator tiká každou hodinu, ale stahuje jen když je potřeba.
- **Smart sync:** zapisují se jen hodiny novější než poslední uložený záznam, takže se nic nezapočítá dvakrát. Neúplný den se při dalším startu dotáhne.
- Čtvrthodiny se sčítají podle **UTC hodiny**, což je korektní i přes přechody letního času. Do lokálních dnů se grupuje až pro zobrazení v senzoru.
- Token je platný do půlnoci – integrace ho automaticky obnovuje.
- Platný status hodnoty je `W` pro všechny typy měřičů (dle API dokumentace 2026-05).
- Dolní mez `from` u `/rest/spotreby` je exkluzivní – integrace to kompenzuje posunem o 15 minut zpět.
- **Známé omezení API:** úplně první čtvrthodina (00:00–00:14) prvního dne, na který má účet oprávnění, není přes API dostupná. API vyžaduje `start > from`, ale dřívější `from` odmítne jako chybu oprávnění. Chybí tedy jednorázově jedna čtvrthodina na začátku historie.
- Odpovědi API se stránkují po 3000 záznamech (`pageStart` je offset záznamu, nikoli číslo stránky). Pole `total` v odpovědi udává jen počet záznamů na aktuální stránce, takže konec se pozná podle neúplné stránky.

## Dokumentace

- [EG.D OpenAPI – Uživatelský návod (PDF)](https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf)

## Poděkování

Integrace byla vyvinuta ve spolupráci s [Claude](https://claude.ai) (Anthropic) – AI asistent se podílel na návrhu architektury, implementaci API klienta, ladění a dokumentaci.
