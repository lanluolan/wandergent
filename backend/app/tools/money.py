from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, computed_field


class Money(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: float = Field(ge=0, allow_inf_nan=False)


def google_money(raw: Any) -> Money | None:
    if not isinstance(raw, dict) or not raw.get("currencyCode"):
        return None
    try:
        nanos_raw = Decimal(str(raw.get("nanos", 0)))
        if not nanos_raw.is_finite() or nanos_raw != nanos_raw.to_integral_value():
            return None
        nanos = int(nanos_raw)
        units = Decimal(raw.get("units", "0"))
        if not units.is_finite() or units != units.to_integral_value() or not 0 <= nanos < 10**9:
            return None
        amount = units + Decimal(nanos) / 10**9
        if amount < 0:
            return None
        return Money(currency=raw["currencyCode"], amount=float(amount))
    except (ValueError, TypeError, InvalidOperation):
        return None


class PriceRange(BaseModel):
    start_price: Money | None = None
    end_price: Money | None = None

    @computed_field
    @property
    def average_price(self) -> Money | None:
        low, high = self.start_price, self.end_price
        if low is None or high is None or low.currency != high.currency or high.amount < low.amount:
            return None
        return Money(currency=low.currency, amount=round((low.amount + high.amount) / 2, 2))


def range_average(value: Any) -> Money | None:
    if not isinstance(value, dict):
        return None
    try:
        return PriceRange.model_validate(value).average_price
    except ValueError:
        return None
