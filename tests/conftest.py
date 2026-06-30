from __future__ import annotations

from poma.config import Settings
from poma.models import OrderResult, ProposedTrade


def make_settings(**overrides: object) -> Settings:
    """Build Settings for tests without depending on a real .env or live secrets."""
    values: dict[str, object] = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "test-chat",
        "DATA_PROVIDER": "fixture",
    }
    values.update(overrides)
    return Settings(**values)


class FakeBroker:
    """In-memory broker that records submissions and reports back filled orders."""

    def __init__(self, positions: list | None = None, cash_balance_usd: float = 10_000.0) -> None:
        self._positions = positions or []
        self._cash_balance_usd = cash_balance_usd
        self.submitted: list[ProposedTrade] | None = None

    def cash_balance_usd(self) -> float:
        return self._cash_balance_usd

    def positions(self) -> list:
        return list(self._positions)

    def submit_trades(self, trades: list[ProposedTrade], status_callback=None) -> list[OrderResult]:
        self.submitted = list(trades)
        results = [
            OrderResult(
                ticker=trade.ticker,
                side=trade.side,
                quantity=trade.quantity,
                notional=trade.notional,
                order_id=index + 1,
                status="Filled",
                filled=trade.quantity,
                average_fill_price=trade.reference_price,
            )
            for index, trade in enumerate(trades)
        ]
        if status_callback is not None:
            for trade, result in zip(trades, results, strict=True):
                status_callback(trade, result)
        return results
