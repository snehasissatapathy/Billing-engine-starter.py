"""
VATCalculator — single-rate VAT (e.g. 19% in Germany).
"""

from decimal import Decimal

from billing_engine.money import Money
from billing_engine.taxes.base import TaxCalculator, TaxContext, TaxBreakdown


class VATCalculator(TaxCalculator):
    def __init__(self, rate: Decimal) -> None:
        if isinstance(rate, float):
            raise TypeError("rate must not be float")

        if rate < Decimal("0") or rate > Decimal("1"):
            raise ValueError("rate must be between 0 and 1")

        self.rate = rate

    def apply(self, taxable: Money, context: TaxContext) -> TaxBreakdown:
        vat = taxable * self.rate

        return TaxBreakdown(
            components=[(f"VAT {self.rate * 100}%", vat)],
            total=vat,
        )
