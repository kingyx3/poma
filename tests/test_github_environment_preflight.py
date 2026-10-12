"""GitHub Environment preflight must fail before provisioning or Gateway configuration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "ops/scripts/validate_github_environment.sh"

PAPER = {
    "INVESTMENT_STRATEGY_WEIGHTS": "core_etf=1.0",
    "CASH_TARGET_PCT": "0.02",
    "CORE_ETF_WEIGHTS": "USMC=1.0",
    "MARKET_CALENDAR": "LSE",
    "REBALANCE_AFTER_OPEN_MINUTES": "390",
    "TELEGRAM_BOT_TOKEN": "secret-token-never-log",
    "TELEGRAM_CHAT_ID": "test-chat",
    "IBKR_ACCOUNT_PAPER": "DU123456",
    "IBKR_LOGIN_ID_PAPER": "paper-user",
    "IBKR_LOGIN_SECRET_PAPER": "secret-paper-login-never-log",
}
LIVE = {
    **{k: v for k, v in PAPER.items() if not k.endswith("_PAPER")},
    "IBKR_ACCOUNT": "U654321",
    "IBKR_LOGIN_ID": "live-user",
    "IBKR_LOGIN_SECRET": "secret-live-login-never-log",
}


def run_check(mode: str, values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECK), mode],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), **values},
    )


@pytest.mark.parametrize(("mode", "settings"), [("paper", PAPER), ("live", LIVE)])
def test_complete_environment_passes_without_leaking_secrets(mode: str, settings: dict[str, str]) -> None:
    result = run_check(mode, settings)
    assert result.returncode == 0, result.stderr
    assert "secret-" not in result.stdout + result.stderr


@pytest.mark.parametrize(("mode", "settings"), [("paper", PAPER), ("live", LIVE)])
@pytest.mark.parametrize(
    "key",
    [
        "INVESTMENT_STRATEGY_WEIGHTS",
        "CASH_TARGET_PCT",
        "CORE_ETF_WEIGHTS",
        "MARKET_CALENDAR",
        "REBALANCE_AFTER_OPEN_MINUTES",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ],
)
def test_missing_required_common_setting_fails(mode: str, settings: dict[str, str], key: str) -> None:
    missing = {k: v for k, v in settings.items() if k != key}
    result = run_check(mode, missing)
    assert result.returncode != 0
    assert key in result.stderr
    assert "secret-" not in result.stderr


@pytest.mark.parametrize("key", ["IBKR_ACCOUNT_PAPER", "IBKR_LOGIN_ID_PAPER", "IBKR_LOGIN_SECRET_PAPER"])
def test_missing_paper_broker_secret_fails(key: str) -> None:
    result = run_check("paper", {k: v for k, v in PAPER.items() if k != key})
    assert result.returncode != 0
    assert key in result.stderr


@pytest.mark.parametrize("key", ["IBKR_ACCOUNT", "IBKR_LOGIN_ID", "IBKR_LOGIN_SECRET"])
def test_missing_live_broker_secret_fails(key: str) -> None:
    result = run_check("live", {k: v for k, v in LIVE.items() if k != key})
    assert result.returncode != 0
    assert key in result.stderr



def test_core_etf_ticker_is_optional_when_no_core_etf_sleeve() -> None:
    values = {
        **PAPER,
        "INVESTMENT_STRATEGY_WEIGHTS": "strategy_a=60%,strategy_b=20%,strategy_c=20%",
    }
    values.pop("CORE_ETF_WEIGHTS")
    result = run_check("paper", values)
    assert result.returncode == 0, result.stderr



def test_missing_many_settings_reports_all_names_without_values() -> None:
    result = run_check("live", {})
    assert result.returncode != 0
    assert "INVESTMENT_STRATEGY_WEIGHTS" in result.stderr
    assert "IBKR_ACCOUNT" in result.stderr
    assert "IBKR_LOGIN_SECRET" in result.stderr


def test_legacy_environment_strategy_var_fails_with_migration_message() -> None:
    result = run_check("paper", {**PAPER, "LEGACY_STRATEGY_ALLOCATIONS": "core_etf=0.98,cash=0.02"})
    assert result.returncode != 0
    assert "migrate" in result.stderr.lower()


def test_workflows_wire_gh_environment_vars_and_preflight_before_deploy() -> None:
    deploy = (ROOT / ".github/workflows/deploy-gcp-vm.yml").read_text()
    gateway = (ROOT / ".github/workflows/ib-gateway-ops.yml").read_text()
    for name in (
        "INVESTMENT_STRATEGY_WEIGHTS",
        "CASH_TARGET_PCT",
        "CORE_ETF_WEIGHTS",
        "MARKET_CALENDAR",
        "REBALANCE_AFTER_OPEN_MINUTES",
    ):
        assert "vars." + name in deploy
        assert "vars." + name in gateway
    for workflow in (deploy, gateway):
        assert "bash ops/scripts/validate_github_environment.sh" in workflow
        assert "secrets.IBKR_LOGIN_SECRET" in workflow
    assert deploy.index("Validate mandatory GitHub Environment") < deploy.index("Resolve generated deployment config")
    assert gateway.index("Validate mandatory GitHub Environment") < gateway.index("Resolve broker login secrets")
