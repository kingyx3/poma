from __future__ import annotations

from typing import Protocol

import requests

from poma.config import Settings, TradingMode
from poma.models import CurrentPosition, ProposedTrade


class Broker(Protocol):
    def positions(self) -> list[CurrentPosition]:
        ...

    def submit_trades(self, trades: list[ProposedTrade]) -> None:
        ...


class DryRunBroker:
    def positions(self) -> list[CurrentPosition]:
        return []

    def submit_trades(self, trades: list[ProposedTrade]) -> None:
        _ = trades


class RemoteExecutorBroker:
    """Calls a small executor service that runs near IB Gateway on the VPS."""

    def __init__(self, settings: Settings) -> None:
        if not settings.executor_endpoint or not settings.executor_api_key:
            raise ValueError("EXECUTOR_ENDPOINT and EXECUTOR_API_KEY are required for remote execution")
        self.endpoint = settings.executor_endpoint.rstrip("/")
        self.api_key = settings.executor_api_key

    def positions(self) -> list[CurrentPosition]:
        response = requests.get(
            f"{self.endpoint}/positions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        return [CurrentPosition(**row) for row in response.json()]

    def submit_trades(self, trades: list[ProposedTrade]) -> None:
        payload = [trade.__dict__ | {"side": trade.side.value} for trade in trades]
        response = requests.post(
            f"{self.endpoint}/orders",
            json={"trades": payload},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60,
        )
        response.raise_for_status()


def build_broker(settings: Settings) -> Broker:
    settings.assert_safe_for_execution()
    if settings.trading_mode == TradingMode.DRY_RUN:
        return DryRunBroker()
    return RemoteExecutorBroker(settings)
