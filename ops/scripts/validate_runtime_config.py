#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from poma.config import ExecutionPriceSource, Settings, TradingMode
from poma.portfolio import build_strategy_capital_plan

# Deploy-time ceiling on EXECUTION_QUOTE_MAX_AGE_SECONDS for paper/live: a rendered .env asking
# for a much staler execution quote than this is very likely a misconfiguration, not intent.
MAX_SAFE_EXECUTION_QUOTE_AGE_SECONDS = 120


def validate(settings: Settings) -> list[str]:
    settings.assert_safe_for_execution()
    capital_plan = build_strategy_capital_plan(
        settings.dry_run_portfolio_value_usd,
        settings.strategy_allocations,
    )

    errors: list[str] = []
    if settings.trading_mode in {TradingMode.PAPER, TradingMode.LIVE} and settings.data_provider != "yahoo":
        errors.append("paper/live trading requires DATA_PROVIDER=yahoo")
    if settings.max_daily_trades < settings.max_holdings:
        errors.append("MAX_DAILY_TRADES must be at least MAX_HOLDINGS for a full bootstrap rebalance")
    if settings.max_order_notional_usd < settings.min_trade_notional_usd:
        errors.append("MAX_ORDER_NOTIONAL_USD must be greater than or equal to MIN_TRADE_NOTIONAL_USD")
    if capital_plan.total_allocated_usd > settings.dry_run_portfolio_value_usd + 1e-6:
        errors.append("total allocated capital exceeds DRY_RUN_PORTFOLIO_VALUE_USD")
    if not capital_plan.tradeable_sleeves():
        errors.append("STRATEGY_ALLOCATIONS must allocate at least one non-cash strategy sleeve")

    if settings.trading_mode in {TradingMode.PAPER, TradingMode.LIVE}:
        if settings.execution_price_source != ExecutionPriceSource.IBKR:
            errors.append(
                "paper/live trading requires EXECUTION_PRICE_SOURCE=ibkr "
                "(the Yahoo snapshot price is not a safe execution reference)"
            )
        if settings.execution_quote_max_age_seconds > MAX_SAFE_EXECUTION_QUOTE_AGE_SECONDS:
            errors.append(
                "EXECUTION_QUOTE_MAX_AGE_SECONDS must be at most "
                f"{MAX_SAFE_EXECUTION_QUOTE_AGE_SECONDS} for paper/live trading"
            )
    if settings.trading_mode == TradingMode.LIVE and settings.allow_delayed_execution_quotes:
        errors.append("LIVE trading must not set ALLOW_DELAYED_EXECUTION_QUOTES=true")
    return errors


def warn(settings: Settings) -> list[str]:
    """Non-fatal production-readiness nudges: safe to deploy with, but worth a human look."""
    warnings: list[str] = []
    if settings.trading_mode == TradingMode.LIVE and settings.max_turnover_pct >= 1.0:
        warnings.append(
            "LIVE trading with MAX_TURNOVER_PCT=100% allows unlimited daily turnover; this is "
            "expected for an initial bootstrap rebalance but should be lowered afterward for "
            "ongoing churn control"
        )
    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a rendered POMA runtime .env file.")
    parser.add_argument("--env-file", default=".env.deploy", type=Path)
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file)
    errors = validate(settings)
    if errors:
        raise SystemExit("; ".join(errors))
    for warning in warn(settings):
        print(f"WARNING: {warning}")

    allocations = settings.strategy_allocation_map()
    capital_plan = build_strategy_capital_plan(settings.dry_run_portfolio_value_usd, allocations)
    sleeves = ", ".join(
        f"{sleeve.name}=${sleeve.capital_usd:,.2f}" for sleeve in capital_plan.tradeable_sleeves()
    )
    print(
        "runtime config ok: "
        f"mode={settings.trading_mode.value} provider={settings.data_provider} "
        f"managed_cap_mode={settings.managed_cap_mode.value} "
        f"strategy_sleeves=[{sleeves}] "
        f"total_allocated=${capital_plan.total_allocated_usd:,.2f} "
        f"dry_run_portfolio_value=${settings.dry_run_portfolio_value_usd:,.2f}"
    )


if __name__ == "__main__":
    main()
