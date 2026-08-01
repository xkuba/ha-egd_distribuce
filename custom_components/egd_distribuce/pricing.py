"""Cenová období s platností od data.

Ceny se v čase mění. Kdyby integrace držela jen jednu aktuální cenu, přepočet
nákladů by starší spotřebu ocenil dnešní cenou a přepsal tím správná historická
data. Proto se zadává seznam období, každé s datem platnosti od – pro každou
čtvrthodinu se použije cena platná v daném okamžiku.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
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
