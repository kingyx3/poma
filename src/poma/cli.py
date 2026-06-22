from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from poma.broker import build_broker
from poma.config import TradingMode, get_settings
from poma.data import build_data_client, utc_run_id
from poma.models import RebalancePlan
from poma.risk import enforce_turnover_limit, generate_trades, validate_targets
from poma.strategy import build_market_cap_targets, select_maintained_or_improved

app = typer.Typer(no_args_is_help=True)
console = Console()


def _write_report(plan: RebalancePlan, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{plan.run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": plan.run_id,
                "targets": [target.__dict__ for target in plan.targets],
                "trades": [trade.__dict__ | {"side": trade.side.value} for trade in plan.trades],
                "warnings": plan.warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return path


@app.command()
def rebalance(dry_run: bool = typer.Option(False, help="Force dry-run mode for this run.")) -> None:
    settings = get_settings()
    if dry_run:
        settings = settings.model_copy(update={"trading_mode": TradingMode.DRY_RUN})

    data_client = build_data_client(settings)
    broker = build_broker(settings)

    current = data_client.current_universe_snapshot()
    previous = data_client.previous_universe_snapshot(settings.rank_lookback_periods)
    selected = select_maintained_or_improved(current, previous)
    targets = build_market_cap_targets(
        selected=selected,
        portfolio_value_usd=settings.portfolio_value_usd,
        cash_buffer_pct=settings.cash_buffer_pct,
        max_position_pct=settings.max_position_pct,
    )

    positions = broker.positions()
    trades = generate_trades(targets, positions, settings.min_trade_notional_usd)
    warnings = validate_targets(targets, settings.max_position_pct)
    warnings.extend(enforce_turnover_limit(trades, settings.portfolio_value_usd, settings.max_turnover_pct))

    plan = RebalancePlan(run_id=utc_run_id(), targets=targets, trades=trades, warnings=warnings)
    report_path = _write_report(plan, settings.report_dir)

    blocked = any("block execution" in warning for warning in warnings)
    if settings.trading_mode == TradingMode.DRY_RUN or blocked:
        console.print(f"Dry run / blocked. Report written to {report_path}")
        for warning in warnings:
            console.print(f"[yellow]WARNING[/yellow] {warning}")
        return

    broker.submit_trades(trades)
    console.print(f"Submitted {len(trades)} trades. Report written to {report_path}")


if __name__ == "__main__":
    app()
