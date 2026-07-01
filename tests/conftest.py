from __future__ import annotations

from poma.config import Settings
from poma.models import AccountSnapshot, OrderResult, ProposedTrade


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

    def __init__(
        self,
        positions: list | None = None,
        *,
        cash_usd: float = 10_000.0,
        net_liquidation_usd: float | None = None,
    ) -> None:
        self._positions = tuple(positions or [])
        self.cash_usd = cash_usd
        self.net_liquidation_usd = net_liquidation_usd
        self.submitted: list[ProposedTrade] | None = None

    def account_snapshot(self) -> AccountSnapshot:
        positions_value = sum(position.market_value for position in self._positions)
        return AccountSnapshot(
            cash_usd=self.cash_usd,
            positions=self._positions,
            positions_market_value_usd=positions_value,
            net_liquidation_usd=self.net_liquidation_usd,
        )

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
