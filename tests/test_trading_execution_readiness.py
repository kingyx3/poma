from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from poma.broker import DryRunBroker, IbkrBroker, IbkrHealth, build_broker
from poma.cli import _assert_execution_ready
from poma.config import Settings
from poma.health import Check, check_ibkr
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
    tickers = [f"T{i:03d}" for i in range(1, settings.max_holdings + 1)]
    targets = build_equal_weight_targets(
        selected=pd.DataFrame({"ticker": tickers}),
        portfolio_value_usd=settings.portfolio_value_usd,
        cash_buffer_pct=settings.cash_buffer_pct,
        max_position_pct=settings.max_position_pct,
    )

    trades, warnings = generate_trades(
        targets=targets,
        current_positions=[],
        latest_prices={ticker: 100.0 for ticker in tickers},
        portfolio_value_usd=settings.portfolio_value_usd,
        min_trade_notional_usd=settings.min_trade_notional_usd,
        min_weight_delta_pct=settings.min_weight_delta_pct,
        limit_offset_bps=settings.limit_offset_bps,
    )

    assert settings.max_turnover_pct == 1.0
    assert warnings == []
    assert len(trades) == settings.max_holdings
    assert sum(trade.notional for trade in trades) / settings.portfolio_value_usd == pytest.approx(0.98)
    assert enforce_turnover_limit(trades, settings.portfolio_value_usd, settings.max_turnover_pct) == []


def test_paper_and_live_modes_use_ibkr_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    paper = _settings(monkeypatch, TRADING_MODE="paper")
    assert isinstance(build_broker(paper), IbkrBroker)

    live = _settings(monkeypatch, TRADING_MODE="live", ALLOW_LIVE_TRADING="true")
    assert isinstance(build_broker(live), IbkrBroker)

    dry_run = _settings(monkeypatch, TRADING_MODE="dry_run")
    assert isinstance(build_broker(dry_run), DryRunBroker)


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
        )

    monkeypatch.setattr("poma.broker.probe_ibkr", fake_probe)

    check = check_ibkr(settings)

    assert not check.ok
    assert "configured IBKR_ACCOUNT=DU1234567 not in ['DU7654321']" in check.detail


def test_pre_trade_readiness_blocks_paper_orders_when_ibkr_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, TRADING_MODE="paper")
    monkeypatch.setattr(
        "poma.cli.check_ibkr",
        lambda _: Check("ibkr", False, "127.0.0.1:7497 unreachable"),
    )

    with pytest.raises(RuntimeError, match="pre-trade IBKR readiness failed"):
        _assert_execution_ready(settings)


def test_pre_trade_readiness_skips_ibkr_for_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, TRADING_MODE="dry_run")
    called = False

    def fail_if_called(_: Settings) -> Check:
        nonlocal called
        called = True
        return Check("ibkr", False, "should not be called")

    monkeypatch.setattr("poma.cli.check_ibkr", fail_if_called)

    _assert_execution_ready(settings)

    assert called is False


def test_effective_deploy_default_exports_full_turnover_before_rendering() -> None:
    resolver = (REPO_ROOT / "ops/scripts/resolve_gcp_deploy_env.sh").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/deploy-gcp-vm.yml").read_text(encoding="utf-8")

    assert 'set_default MAX_TURNOVER_PCT "1.0"' in resolver
    assert "source ops/scripts/resolve_gcp_deploy_env.sh" in workflow
