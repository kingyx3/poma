from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from conftest import make_settings

from poma.broker import (
    BROKER_UNAVAILABLE_STATUS,
    DryRunBroker,
    IbkrBroker,
    IbkrHealth,
    build_broker,
)
from poma.config import Settings
from poma.data import FixtureMarketDataClient
from poma.engine import NO_ORDERS_ACCEPTED_STATUS, RebalanceEngine
from poma.health import check_ibkr
from poma.models import OrderResult, OrderSide, ProposedTrade
from poma.order_status_alerts import order_status_alert
from poma.portfolio import CURRENT_STRATEGY_NAME, build_strategy_capital_plan
from poma.risk import enforce_turnover_limit, generate_trades
from poma.strategy import build_equal_weight_targets

REPO_ROOT = Path(__file__).resolve().parents[1]


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    monkeypatch.delenv("MAX_TURNOVER_PCT", raising=False)
    env = {
        "APP_ENV": "test",
        "TRADING_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "DATA_PROVIDER": "fixture",
        "IBKR_ACCOUNT": "DU1234567",
        "IBKR_HOST": "127.0.0.1",
        "IBKR_PORT": "7497",
        "IBKR_CLIENT_ID": "101",
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "123456",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_default_turnover_allows_initial_full_paper_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    capital_plan = build_strategy_capital_plan(
        settings.portfolio_value_usd,
        settings.strategy_allocations,
    )
    strategy_capital = capital_plan.capital_for(CURRENT_STRATEGY_NAME)
    tickers = [f"T{i:03d}" for i in range(1, settings.max_holdings + 1)]
    targets = build_equal_weight_targets(
        selected=pd.DataFrame({"ticker": tickers}),
        portfolio_value_usd=strategy_capital.capital_usd,
        max_position_pct=settings.max_position_pct,
    )

    trades, warnings = generate_trades(
        targets=targets,
        current_positions=[],
        latest_prices={ticker: 100.0 for ticker in tickers},
        portfolio_value_usd=strategy_capital.capital_usd,
        min_trade_notional_usd=settings.min_trade_notional_usd,
        min_weight_delta_pct=settings.min_weight_delta_pct,
        limit_offset_bps=settings.limit_offset_bps,
    )

    assert settings.max_turnover_pct == 1.0
    assert settings.max_consecutive_order_acceptance_failures == 3
    assert strategy_capital.allocation_pct == pytest.approx(0.98)
    assert warnings == []
    assert len(trades) == settings.max_holdings
    assert sum(trade.notional for trade in trades) / settings.portfolio_value_usd == pytest.approx(0.98)
    assert enforce_turnover_limit(trades, strategy_capital.capital_usd, settings.max_turnover_pct) == []


def test_paper_and_live_modes_use_expected_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(build_broker(_settings(monkeypatch, TRADING_MODE="paper")), IbkrBroker)
    assert isinstance(
        build_broker(_settings(monkeypatch, TRADING_MODE="live", ALLOW_LIVE_TRADING="true")),
        IbkrBroker,
    )
    assert isinstance(build_broker(_settings(monkeypatch, TRADING_MODE="dry_run")), DryRunBroker)


def test_paper_mode_requires_configured_ibkr_account(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, TRADING_MODE="paper", IBKR_ACCOUNT="")

    with pytest.raises(RuntimeError, match="paper trading requires IBKR_ACCOUNT"):
        build_broker(settings)


def test_live_mode_requires_explicit_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, TRADING_MODE="live", ALLOW_LIVE_TRADING="false")

    with pytest.raises(RuntimeError, match="LIVE trading requires ALLOW_LIVE_TRADING=true"):
        build_broker(settings)


def test_ibkr_health_fails_when_configured_account_is_not_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, TRADING_MODE="paper", IBKR_ACCOUNT="DU1234567")

    def fake_probe(_: Settings) -> IbkrHealth:
        return IbkrHealth(
            connected=True,
            accounts=["DU7654321"],
            server_time="2026-06-28T00:00:00Z",
            stock_positions=0,
            trading_permissions_ok=True,
            trading_permissions_message="what-if order preview accepted for AAPL",
        )

    monkeypatch.setattr("poma.broker.probe_ibkr", fake_probe)

    check = check_ibkr(settings)

    assert not check.ok
    assert "configured IBKR_ACCOUNT=DU1234567 not in ['DU7654321']" in check.detail


def test_final_order_status_alert_message_includes_status_and_fill_details() -> None:
    message = order_status_alert(
        "2026-06-29",
        OrderResult(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            notional=980.0,
            order_id=123,
            status="Filled",
            filled=5.0,
            average_fill_price=196.0,
        ),
    )

    assert message == (
        "🔔 Order status update\n"
        "Session: 2026-06-29\n"
        "Status: Filled\n"
        "Order: BUY AAPL\n"
        "Filled: 5/5\n"
        "Notional: $980\n"
        "Average fill: $196.00\n"
        "Order ID: 123"
    )


def test_order_status_alert_includes_diagnostic_message() -> None:
    message = order_status_alert(
        "2026-06-29",
        OrderResult(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            notional=980.0,
            order_id=123,
            status="Failed",
            filled=0.0,
            average_fill_price=None,
            message="broker rejected order",
        ),
    )

    assert message.endswith("Detail: broker rejected order")


def test_dry_run_broker_emits_status_callback() -> None:
    trade = ProposedTrade(
        ticker="AAPL",
        side=OrderSide.BUY,
        quantity=5.0,
        notional=980.0,
        reference_price=196.0,
        limit_price=196.20,
        reason="rebalance_to_target_weight",
    )
    captured: list[OrderResult] = []

    results = DryRunBroker().submit_trades(
        [trade],
        status_callback=lambda _trade, result: captured.append(result),
    )

    assert results[0].status == "dry_run"
    assert [result.status for result in captured] == ["dry_run"]


def test_ibkr_broker_does_not_emit_created_when_connection_drops_before_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIB:
        def __init__(self) -> None:
            self.connected = False
            self.place_order_calls = 0
            self.RequestTimeout = None

        def connect(self, *_args, **_kwargs) -> None:
            self.connected = True

        def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync API
            return self.connected

        def managedAccounts(self) -> list[str]:  # noqa: N802 - mirrors ib_insync API
            return ["DU1234567"]

        def reqCurrentTime(self) -> str:  # noqa: N802 - mirrors ib_insync API
            return "2026-06-29T13:40:00Z"

        def whatIfOrder(self, *_args, **_kwargs):  # noqa: N802, ANN202 - ib_insync shape
            return object()

        def placeOrder(self, *_args, **_kwargs):  # noqa: N802, ANN202 - ib_insync shape
            self.place_order_calls += 1
            self.connected = False
            raise RuntimeError("Not connected")

        def disconnect(self) -> None:
            self.connected = False

    instances: list[FakeIB] = []

    def fake_ib() -> FakeIB:
        instance = FakeIB()
        instances.append(instance)
        return instance

    monkeypatch.setattr("poma.broker.IB", fake_ib)
    broker = IbkrBroker(_settings(monkeypatch))
    trades = [
        ProposedTrade("NVDA", OrderSide.BUY, 0.5, 98.0, 196.0, 196.20, "rebalance"),
        ProposedTrade("NVS", OrderSide.BUY, 0.6, 98.0, 160.0, 160.16, "rebalance"),
    ]
    captured: list[OrderResult] = []

    results = broker.submit_trades(
        trades,
        status_callback=lambda _trade, result: captured.append(result),
    )

    assert instances[0].place_order_calls == 1
    assert [result.status for result in results] == [BROKER_UNAVAILABLE_STATUS] * 2
    assert [result.status for result in captured] == [BROKER_UNAVAILABLE_STATUS] * 2
    assert all(result.order_id is None for result in results)
    assert all(result.filled == 0 for result in results)
    assert "Created" not in [result.status for result in captured]
    assert "no further orders submitted" in str(results[0].message)


def test_engine_marks_all_cancelled_orders_as_no_orders_accepted() -> None:
    class CancelledBroker:
        def cash_balance_usd(self) -> float:
            return 10_000.0

        def positions(self) -> list:
            return []

        def submit_trades(
            self,
            trades: list[ProposedTrade],
            status_callback=None,
        ) -> list[OrderResult]:
            results = [
                OrderResult(
                    ticker=trade.ticker,
                    side=trade.side,
                    quantity=trade.quantity,
                    notional=trade.notional,
                    order_id=index + 1,
                    status="Cancelled",
                    filled=0.0,
                    average_fill_price=None,
                    message="cancelled by broker",
                )
                for index, trade in enumerate(trades)
            ]
            if status_callback is not None:
                for trade, result in zip(trades, results, strict=True):
                    status_callback(trade, result)
            return results

    engine = RebalanceEngine(
        make_settings(
            TRADING_MODE="paper",
            IBKR_ACCOUNT="DU1234567",
            MAX_TURNOVER_PCT=1.0,
            MAX_ORDER_NOTIONAL_USD=100_000.0,
        ),
        data_client=FixtureMarketDataClient(),
        broker=CancelledBroker(),
    )

    outcome = engine.run("session", "run")

    assert outcome.executed
    assert outcome.status == NO_ORDERS_ACCEPTED_STATUS


def test_effective_deploy_default_exports_full_turnover_before_rendering() -> None:
    resolver = (REPO_ROOT / "ops/scripts/resolve_gcp_deploy_env.sh").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml").read_text(encoding="utf-8")

    assert 'set_default MAX_TURNOVER_PCT "1.0"' in resolver
    assert "source ops/scripts/resolve_gcp_deploy_env.sh" in workflow


def test_paper_deploy_maps_runtime_account_from_paper_secret() -> None:
    workflow = (REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml").read_text(encoding="utf-8")
    paper_case = workflow.split("paper)", 1)[1].split(";;", 1)[0]

    assert "IBKR_ACCOUNT_PAPER GitHub Environment secret is required when TRADING_MODE=paper" in paper_case
    assert 'IBKR_ACCOUNT_PAPER: ${{ secrets.IBKR_ACCOUNT_PAPER }}' in workflow
    assert 'set_env IBKR_ACCOUNT "${IBKR_ACCOUNT_PAPER}"' in paper_case


def test_deploy_validates_rendered_runtime_config() -> None:
    workflow = (REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml").read_text(encoding="utf-8")
    validator = REPO_ROOT / "ops/scripts/validate_runtime_config.py"

    assert validator.exists()
    assert "python ops/scripts/validate_runtime_config.py --env-file .env.deploy" in workflow
