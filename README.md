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

### Náklady
- ✅ Statistika nákladů v Kč pro Energy Dashboard – včetně celé historie
- ✅ Tarif VT/NT z veřejného **kalendáře HDO** (funguje i zpětně)
- ✅ Cenová období s platností od data – zdražení nepřepíše starší náklady
- ✅ Senzor měsíčních nákladů včetně stálé platby

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

| Sekce | Parametr | Výchozí | Popis |
|---|---|---|---|
| Stahování dat | Hodina stahování | 17 | Data se stáhnou při prvním ticku v nebo po zadané hodině |
| Stahování dat | Datum počátku historie | 30 dní zpět | Od tohoto data se stáhne zpětná historie |
| Tarif HDO | Způsob určení tarifu | jednotarif | Viz [Náklady na elektřinu](#náklady-na-elektřinu) |
| Tarif HDO | Obnova rozvrhu | 7 dní | Jak často znovu stáhnout kalendář HDO (1–90) |
| Tarif HDO | Entita s tarifem z elektroměru | – | Volitelné, viz níže |
| Cenová období | Ceny a stálá platba | – | Seznam období s platností od data |
| Vyúčtování | Datum posledního vyúčtování | – | Volitelné, zapne senzor odhadu faktury |
| Zálohy | Rozpis záloh | – | Volitelné, zapne senzor přeplatek/nedoplatek |

Změny se ukládají až volbou **„Uložit a zavřít"**, takže lze v jednom průchodu přidat víc cenových období.

Integrace se po změně nastavení **znovu načte jen když je to nutné** – tedy když přibude nebo zmizí senzor, případně když se změní kód HDO. U změny cen nebo data vyúčtování se jen přepočítají data a entity nikam nezmizí.

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
| **Náklady na spotřebu (Kč)** | `egd_distribuce:<EAN>_consumption_cost` |

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

## Náklady na elektřinu

Integrace umí spočítat, kolik odebraná energie stála, a to i zpětně pro celou historii.

### Proč vlastní statistika nákladů

Home Assistant umí náklady dopočítat sám z ceny za kWh, ale **jen pro zdroje, které jsou skutečné entity**. Naše spotřeba je externí statistika, a tu HA odmítá s hláškou *„Entity or number price is not supported for external statistics. Use stat_cost instead."*

Integrace proto zapisuje vlastní hodinovou statistiku nákladů `egd_distribuce:<EAN>_consumption_cost` v Kč. V panelu energie ji vyberte volbou **„Použít entitu sledující celkové náklady"**, ne polem s cenou.

### Tarif VT/NT

API měřených dat tarif neobsahuje – spotřeba chodí jako jeden profil. Tarif se proto bere z veřejného kalendáře HDO (`hdo.distribuce24.cz`). Protože je to **rozvrh, ne živý signál**, lze podle něj určit tarif i pro data stará měsíce.

Nastavení najdete v **Konfigurovat → Tarif HDO**:

| Režim | Co zadat | Kde to najít |
|---|---|---|
| Jednotarif | nic | jedna cena pro celý den |
| Smart | kód typu `Cd2526_2` | na přijímači HDO nebo v aplikaci Distribuce24 |
| Klasický | PSČ + příkazový kód A/B/DP | na přijímači HDO u elektroměru |

Formát kódu si ověříte na [hdo.distribuce24.cz/casy](https://hdo.distribuce24.cz/casy).

#### Když se integrace doptá na rozvrh

Jeden kód často řídí víc relé. Vedle tarifního relé bývá ještě samostatný obvod pro ohřev vody (`TUV`), který jen spíná bojler a na cenu za kWh nemá vliv. Integrace proto u každé volby ukazuje **počet hodin nízkého tarifu za den**:

```
D57d-relé1-PVTC,PV (AD579, SM) – 20 h NT/den    <- tarifní relé
D57d-relé2-TUV     (AD579, SM) –  8 h NT/den    <- jen bojler
```

Vyberte ten, jehož počet hodin odpovídá vaší sazbě (např. D57d má 20 h, D25d/D26d 8 h). Nabídka je seřazená sestupně, takže tarifní rozvrh bývá první. Pokud mají obě volby stejný počet hodin, rozhoduje název: `TAR` je tarifní obvod, `TUV` ohřev vody.

Sezónní varianty (zima/léto) řeší integrace sama – vybíráte jen relé.

#### Tarif přímo z elektroměru (doporučeno)

Máte-li chytrý elektroměr čtený lokálně (např. XT211 přes ESPHome a DLMS/RS485), vyplňte v konfiguraci **„Entita s tarifem z elektroměru"**. Získáte tím dvě věci:

- **Živé senzory berou tarif z měřiče**, ne z kalendáře. Měřič hlásí stav, podle kterého se skutečně účtuje, takže odpadá jakákoli nejistota v předpovědi.
- **Integrace hlídá správnost kódu HDO.** Průběžně porovnává, co říká měřič, s tím, co předpovídá kalendář. Při neshodě zapíše varování do logu. Špatně zadaný kód nebo špatně zvolené relé je jinak skoro nezjistitelné a tiše by znehodnotilo všechny spočítané náklady.

Rozpozná `VT`/`NT`, `T2`/`T3` i syrové `2`/`3` (dle datasheetu XT211 je 2 = VT, 3 = NT). Jinou hodnotu ohlásí v logu a použije kalendář – stejně tak když je entita nedostupná.

U ESPHome se hodí hodnotu přemapovat už na zařízení, ať je čitelná i tam:

```yaml
    filters:
      - map:
          - "T2 -> VT"
          - "T3 -> NT"
```

Kolem okamžiku přepnutí se pár minut neporovnává – měřič a kalendář se tam legitimně liší a hlásit to jako chybu by bylo matoucí.

Historické náklady se počítají **vždy z kalendáře** – měřič umí říct jen „teď".

Atributy senzoru tarifu ukazují `zdroj` (meter/calendar), `tarif_z_meraku`, `tarif_z_kalendare` a `kalendar_souhlasi`.

#### Obnova rozvrhu

Kalendář se stahuje jednou za **7 dní** (nastavitelné 1–90 v poli „Obnova rozvrhu").

Sezónní přechody na obnově nezávisí – integrace drží v paměti všechny sezóny naráz a vybírá podle data, takže přechod zima/léto proběhne sám i bez jediného stažení. Obnova je kvůli tomu, že distributor může změnit **samotné časy**: rok `9999` v platnosti znamená „opakuje se každý rok", nikoli „nikdy se nezmění". Změnu integrace zaloguje.

Stažení stojí ~44 kB (gzip) a jde na jiný endpoint než měřená data, takže nezatěžuje váš API účet.

#### Když rozvrh není znám

Část rozvrhů má v platnosti konkrétní rok, ne `9999`, takže může vypršet. Pro dny bez platného rozvrhu se náklady **nepočítají vůbec** a do logu jde varování – radši žádný údaj než všechno naúčtované ve vysokém tarifu.

Pozor na rozdíl: den, kdy nízký tarif prostě není (víkendová sazba D61d nemá pondělí až čtvrtek), je normálně oceněný celý ve VT. Neznámý rozvrh je něco jiného než nulový.

### Cenová období

Ceny se zadávají jako **seznam období, každé s datem platnosti od**. Pro každou čtvrthodinu se použije cena platná v danou dobu.

Díky tomu zdražení neovlivní už spočítané náklady: přidáte nové období od data zdražení a starší data si podrží svou původní cenu. Přepočítávat nemusíte nic.

V **Konfigurovat → Cenová období** zadejte:

| Pole | Význam |
|---|---|
| Platnost od | datum, od kterého ceny platí |
| Cena VT | výsledná cena za kWh vč. distribuce, poplatků a daní |
| Cena NT | cena v nízkém tarifu (u jednotarifu nechte prázdné) |
| Stálá měsíční platba | započítá se jen do senzoru měsíčních nákladů |

Spotřeba z doby **před prvním obdobím se záměrně neoceňuje** – radši žádný údaj než odhad.

### Senzory kolem ceny

Vzniknou, jakmile zadáte aspoň jedno cenové období:

| Senzor | Hodnota |
|---|---|
| Náklady tento měsíc | spotřeba × cena + naběhlá stálá platba, reset k 1. dni |
| Náklady od vyúčtování | odhad další faktury (jen se zadaným datem vyúčtování) |
| Aktuální cena | Kč/kWh platná právě teď dle tarifu a období |
| Aktuální tarif | VT / NT (jen při dvoutarifu) |
| Následující změna tarifu | čas nejbližšího přepnutí (jen při dvoutarifu) |

Senzory tarifu a ceny se překreslují **přesně v okamžik přepnutí**, ne až s hodinovým tikem integrace – rozvrhy přepínají i na půlhodinách a desetiminutách. Rozvrh je v paměti, takže to nestojí žádné volání API.

Senzor měsíčních nákladů do panelu energie **nepatří** – stálá platba není za kWh a se statistikou nákladů by se dublovala.

### Dvě nákladové statistiky

| Statistika | Obsahuje | Kam patří |
|---|---|---|
| `egd_distribuce:<EAN>_consumption_cost` | jen cena za odebranou energii | **Energy Dashboard** |
| `egd_distribuce:<EAN>_total_cost` | navíc stálá platba | grafy celkových nákladů |

Do panelu energie patří ta první – stálá platba není za kWh a zkreslila by přepočty na Kč/kWh. Druhá odpovídá tomu, co reálně zaplatíte; stálá platba je v ní rozpuštěná rovnoměrně do hodin, takže součet za libovolné období vychází přesně.

Měsíční historii celkových nákladů dostanete kartou:

```yaml
type: statistics-graph
entities:
  - egd_distribuce:<EAN>_total_cost
period: month
chart_type: bar
stat_types:
  - change
```

### Vyúčtování a odhad další faktury

V **Konfigurovat → Vyúčtování** zadejte datum poslední faktury. Vznikne senzor **Náklady od vyúčtování**, který ukazuje, kolik od té doby naběhlo – tedy odhad toho, co přijde příště.

### Zálohy

V **Konfigurovat → Zálohy** zadejte rozpis. Nezadávají se jednotlivé platby, ale jen **okamžiky, kdy se částka mění** – stejně jako u cen:

```
od 10.10.2024:  5 450 Kč
od 11.11.2024:  5 780 Kč
od 10.01.2025:  5 080 Kč
```

Den v měsíci se převezme ze zadaného data. Platí pravidlo **jedna záloha za kalendářní měsíc**, částka i den podle posledního platného záznamu. Rozpis najdete na faktuře v tabulce „Předpis budoucích stanovených zálohových plateb".

Se zadaným rozpisem vznikne druhý senzor **Rozdíl proti zálohám**: kladná hodnota je nedoplatek, záporná přeplatek.

> Ověřeno proti skutečné faktuře E.ON: tři řádky rozpisu reprodukovaly všech dvanáct plateb za roční období včetně součtu 62 730 Kč na korunu.

| Atribut | Význam |
|---|---|
| `od_data`, `pocet_dni` | období, za které se počítá |
| `naklady_za_energii` | spotřeba × cena dle tarifu |
| `stale_platby` | stálá platba naběhlá po dnech |
| `pocet_zaloh`, `zaplacene_zalohy` | jen se zadaným rozpisem záloh |
| `posledni_zaloha`, `dalsi_zaloha`, `dalsi_castka` | kdy platba proběhla a kdy přijde další |
| `rozdil`, `stav` | kladné = nedoplatek, záporné = přeplatek |

Je to **odhad, ne faktura** – nezahrnuje dodatečné opravy od dodavatele a data z EG.D chodí se zpožděním jednoho dne.

**Stálá platba se všude počítá po dnech**, ne po celých měsících: denní podíl = měsíční platba ÷ počet dní v daném měsíci. Součet přes celý měsíc dá přesně měsíční platbu, a neúplné měsíce vyjdou správně i když vyúčtování přijde k libovolnému datu. Změní-li se platba mezi cenovými obdobími, každý den se počítá tou svou.

> Ověřeno proti faktuře E.ON: sloupec „Počet jednotek" u položky Stálý plat uváděl 3,133 / 3,233 / 5,733 měsíce a náš výpočet dává přesně totéž. Dodavatel používá stejný algoritmus.

### Přesnost

Náklady se počítají na **čtvrthodinách** a teprve pak sčítají do hodin. Některé rozvrhy HDO přepínají na desetiminutách, takže čtvrthodina může spadat do obou tarifů – energie se v takovém případě rozdělí poměrem překryvu, ne binárně.

Historická přesnost stojí na aktuálně publikovaném rozvrhu. Pokud EG.D časy v minulosti měnil, na stará data se použije dnešní verze.

### Přepočet po opravě ceny

Zavedení nové ceny přepočet nevyžaduje. Potřebujete ho jen když **opravíte cenu už proběhlého období**:

1. **Vývojářské nástroje → Statistiky** → smažte `egd_distribuce:<EAN>_consumption_cost`
2. Integraci znovu načtěte

Integrace pozná, že náklady zaostávají za spotřebou, a dopočítá je znovu – s cenami podle jednotlivých období.

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
