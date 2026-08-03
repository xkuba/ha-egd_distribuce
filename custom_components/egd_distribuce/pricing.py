"""Cenová období s platností od data.

Ceny se v čase mění. Kdyby integrace držela jen jednu aktuální cenu, přepočet
nákladů by starší spotřebu ocenil dnešní cenou a přepsal tím správná historická
data. Proto se zadává seznam období, každé s datem platnosti od – pro každou
čtvrthodinu se použije cena platná v daném okamžiku.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Klíče v uloženém dictu (options)
KEY_VALID_FROM = "valid_from"
KEY_PRICE_VT = "price_vt"
KEY_PRICE_NT = "price_nt"
KEY_MONTHLY_FEE = "monthly_fee"


class PricingError(Exception):
    """Neplatná definice cenového období."""


@dataclass(frozen=True, order=True)
class PricePeriod:
    """Ceny platné od zadaného data do začátku dalšího období."""

    valid_from: date
    price_vt: float
    price_nt: float
    monthly_fee: float

    def price_for(self, nt_fraction: float) -> float:
        """Cena za kWh při zadaném podílu nízkého tarifu (0.0 – 1.0)."""
        nt_fraction = min(1.0, max(0.0, nt_fraction))
        return self.price_nt * nt_fraction + self.price_vt * (1.0 - nt_fraction)

    def as_dict(self) -> dict[str, Any]:
        return {
            KEY_VALID_FROM: self.valid_from.isoformat(),
            KEY_PRICE_VT: self.price_vt,
            KEY_PRICE_NT: self.price_nt,
            KEY_MONTHLY_FEE: self.monthly_fee,
        }

    @property
    def label(self) -> str:
        return (
            f"od {self.valid_from.isoformat()}: "
            f"VT {self.price_vt:g} / NT {self.price_nt:g} Kč/kWh, "
            f"stálá {self.monthly_fee:g} Kč/měs"
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PricePeriod:
        try:
            return cls(
                valid_from=date.fromisoformat(str(raw[KEY_VALID_FROM])),
                price_vt=float(raw[KEY_PRICE_VT]),
                price_nt=float(raw[KEY_PRICE_NT]),
                monthly_fee=float(raw.get(KEY_MONTHLY_FEE, 0.0)),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise PricingError(f"Neplatné cenové období: {raw!r}") from err


class PriceList:
    """Seznam cenových období seřazený podle platnosti."""

    def __init__(self, periods: list[PricePeriod]) -> None:
        self._periods = sorted(periods)

    def __bool__(self) -> bool:
        return bool(self._periods)

    def __len__(self) -> int:
        return len(self._periods)

    def __iter__(self):
        return iter(self._periods)

    @classmethod
    def from_options(cls, raw: Any) -> PriceList:
        """Načte období z options; nevalidní záznamy přeskočí s varováním."""
        periods: list[PricePeriod] = []
        for item in raw or []:
            try:
                periods.append(PricePeriod.from_dict(item))
            except PricingError as err:
                _LOGGER.warning("EGD: %s", err)
        return cls(periods)

    def as_options(self) -> list[dict[str, Any]]:
        return [p.as_dict() for p in self._periods]

    def for_date(self, day: date) -> PricePeriod | None:
        """Období platné pro zadaný den (poslední s valid_from <= day).

        Vrací None pro dny před prvním obdobím – takovou spotřebu záměrně
        neoceňujeme, aby se do nákladů nedostal odhad.
        """
        result: PricePeriod | None = None
        for period in self._periods:
            if period.valid_from <= day:
                result = period
            else:
                break
        return result

    def covers(self, day: date) -> bool:
        return self.for_date(day) is not None

    @property
    def first_valid_from(self) -> date | None:
        return self._periods[0].valid_from if self._periods else None

    def standing_charge(self, date_from: date, date_to: date) -> float:
        """Stálá platba naběhlá za období date_from – date_to (oba dny včetně).

        Počítá se **po dnech**, ne po celých měsících: vyúčtování přichází
        k libovolnému datu a faktury stálý plat u neúplných měsíců poměrují.
        Denní podíl = měsíční platba / počet dní v daném měsíci, takže součet
        přes celý měsíc dá přesně měsíční platbu.

        Dny před prvním cenovým obdobím se nezapočítají – stejně jako
        u spotřeby raději nic než odhad.
        """
        if date_to < date_from:
            return 0.0

        total = 0.0
        day = date_from
        while day <= date_to:
            period = self.for_date(day)
            if period is not None and period.monthly_fee:
                days_in_month = monthrange(day.year, day.month)[1]
                total += period.monthly_fee / days_in_month
            day += timedelta(days=1)
        return round(total, 4)

    def daily_standing_charge(self, day: date) -> float:
        """Podíl stálé platby připadající na jeden den."""
        period = self.for_date(day)
        if period is None or not period.monthly_fee:
            return 0.0
        return period.monthly_fee / monthrange(day.year, day.month)[1]


# Klíče cenového období zálohy
KEY_ADVANCE_FROM = "valid_from"
KEY_ADVANCE_AMOUNT = "amount"


@dataclass(frozen=True, order=True)
class AdvancePeriod:
    """Výše zálohy platná od zadaného data.

    Den v měsíci se bere z tohoto data – dodavatel předepisuje zálohy vždy
    ke stejnému dni, takže stačí zadat jen okamžik, kdy se částka mění.
    """

    valid_from: date
    amount: float

    def as_dict(self) -> dict[str, Any]:
        return {
            KEY_ADVANCE_FROM: self.valid_from.isoformat(),
            KEY_ADVANCE_AMOUNT: self.amount,
        }

    @property
    def label(self) -> str:
        return (
            f"od {self.valid_from.isoformat()}: {self.amount:g} Kč "
            f"(vždy {self.valid_from.day}. v měsíci)"
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AdvancePeriod:
        try:
            return cls(
                valid_from=date.fromisoformat(str(raw[KEY_ADVANCE_FROM])),
                amount=float(raw[KEY_ADVANCE_AMOUNT]),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise PricingError(f"Neplatný rozpis zálohy: {raw!r}") from err


class AdvanceSchedule:
    """Rozpis záloh – generuje jednotlivé platby z období platnosti.

    Pravidlo: **jedna záloha za kalendářní měsíc**, částka i den v měsíci podle
    posledního rozpisu platného v tom měsíci. Tím se ošetří i přechod, kdy se
    zároveň mění částka i den – jinak by v měsíci změny vznikly platby dvě.

    Ověřeno proti skutečné faktuře: rozpis o třech řádcích reprodukoval všech
    dvanáct plateb za rok se správnými částkami a celkovým součtem na korunu.
    """

    def __init__(self, periods: list[AdvancePeriod]) -> None:
        self._periods = sorted(periods)

    def __bool__(self) -> bool:
        return bool(self._periods)

    def __len__(self) -> int:
        return len(self._periods)

    def __iter__(self):
        return iter(self._periods)

    @classmethod
    def from_options(cls, raw: Any) -> AdvanceSchedule:
        periods: list[AdvancePeriod] = []
        for item in raw or []:
            try:
                periods.append(AdvancePeriod.from_dict(item))
            except PricingError as err:
                _LOGGER.warning("EGD: %s", err)
        return cls(periods)

    def as_options(self) -> list[dict[str, Any]]:
        return [p.as_dict() for p in self._periods]

    def _for_month_end(self, month_end: date) -> AdvancePeriod | None:
        """Rozpis platný pro měsíc končící zadaným dnem."""
        result: AdvancePeriod | None = None
        for period in self._periods:
            if period.valid_from <= month_end:
                result = period
            else:
                break
        return result

    def payments(self, date_from: date, date_to: date) -> list[tuple[date, float]]:
        """Jednotlivé zálohy splatné v zadaném období."""
        if not self._periods or date_to < date_from:
            return []

        result: list[tuple[date, float]] = []
        year, month = date_from.year, date_from.month

        while date(year, month, 1) <= date_to:
            days = monthrange(year, month)[1]
            period = self._for_month_end(date(year, month, days))
            if period is not None:
                # Krátký měsíc – den 31 spadne na poslední den v měsíci
                due = date(year, month, min(period.valid_from.day, days))
                if date_from <= due <= date_to:
                    result.append((due, period.amount))

            month += 1
            if month > 12:
                month, year = 1, year + 1

        return result

    def total_paid(self, date_from: date, date_to: date) -> float:
        """Součet záloh splatných v období."""
        return round(sum(amount for _, amount in self.payments(date_from, date_to)), 2)

    def next_payment(self, after: date) -> tuple[date, float] | None:
        """Nejbližší záloha po zadaném dni (pro informaci v atributech)."""
        upcoming = self.payments(after, date(after.year + 2, after.month, 1))
        for due, amount in upcoming:
            if due > after:
                return due, amount
        return None
