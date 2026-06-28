#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from poma.config import Settings, TradingMode
from poma.portfolio import build_strategy_capital_plan


def validate(settings: Settings) -> list[str]:
    settings.assert_safe_for_execution()
    capital_plan = build_strategy_capital_plan(
        settings.portfolio_value_usd,
        settings.strategy_allocations,
    )
    strategy_capital = capital_plan.capital_for(settings.active_strategy)

    errors: list[str] = []
    if settings.trading_mode in {TradingMode.PAPER, TradingMode.LIVE} and settings.data_provider != "yahoo":
        errors.append("paper/live trading requires DATA_PROVIDER=yahoo")
    if settings.max_daily_trades < settings.max_holdings:
        errors.append("MAX_DAILY_TRADES must be at least MAX_HOLDINGS for a full bootstrap rebalance")
    if settings.max_order_notional_usd < settings.min_trade_notional_usd:
        errors.append("MAX_ORDER_NOTIONAL_USD must be greater than or equal to MIN_TRADE_NOTIONAL_USD")
    if strategy_capital.capital_usd > settings.portfolio_value_usd:
        errors.append("active strategy capital exceeds PORTFOLIO_VALUE_USD")
    if capital_plan.total_allocated_usd > settings.portfolio_value_usd + 1e-6:
        errors.append("total allocated capital exceeds PORTFOLIO_VALUE_USD")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a rendered POMA runtime .env file.")
    parser.add_argument("--env-file", default=".env.deploy", type=Path)
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file)
    errors = validate(settings)
    if errors:
        raise SystemExit("; ".join(errors))

    allocations = settings.strategy_allocation_map()
    capital_plan = build_strategy_capital_plan(settings.portfolio_value_usd, allocations)
    print(
        "runtime config ok: "
        f"mode={settings.trading_mode.value} provider={settings.data_provider} "
        f"active_strategy={settings.active_strategy} "
        f"active_capital=${capital_plan.capital_for(settings.active_strategy).capital_usd:,.2f} "
        f"total_allocated=${capital_plan.total_allocated_usd:,.2f} "
        f"portfolio_cap=${settings.portfolio_value_usd:,.2f}"
    )


if __name__ == "__main__":
    main()
