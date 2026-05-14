from decimal import Decimal
from typing import Protocol


class FinancialReportLike(Protocol):
    revenue: Decimal | None
    ebitda: Decimal | None
    total_debt: Decimal | None
    cash: Decimal | None
    equity: Decimal | None
    short_term_debt: Decimal | None
    operating_cash_flow: Decimal | None
    net_profit: Decimal | None
    interest_expense: Decimal | None


class FinancialRatiosService:
    @staticmethod
    def safe_divide(
        numerator: Decimal | int | float | None,
        denominator: Decimal | int | float | None,
    ) -> Decimal | None:
        if numerator is None or denominator is None:
            return None
        numerator_decimal = Decimal(str(numerator))
        denominator_decimal = Decimal(str(denominator))
        if denominator_decimal == 0:
            return None
        return numerator_decimal / denominator_decimal

    def calculate(self, report: FinancialReportLike) -> dict[str, Decimal | None]:
        net_debt = self._subtract(report.total_debt, report.cash)
        return {
            "net_debt_to_ebitda": self.safe_divide(net_debt, report.ebitda),
            "debt_to_equity": self.safe_divide(report.total_debt, report.equity),
            "interest_coverage": self.safe_divide(
                report.ebitda, report.interest_expense
            ),
            "cash_to_short_term_debt": self.safe_divide(
                report.cash, report.short_term_debt
            ),
            "operating_cash_flow_to_total_debt": self.safe_divide(
                report.operating_cash_flow, report.total_debt
            ),
            "net_profit_margin": self.safe_divide(report.net_profit, report.revenue),
        }

    @staticmethod
    def _subtract(
        left: Decimal | int | float | None,
        right: Decimal | int | float | None,
    ) -> Decimal | None:
        if left is None or right is None:
            return None
        return Decimal(str(left)) - Decimal(str(right))

