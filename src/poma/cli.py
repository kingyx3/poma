from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from poma.broker import build_broker
from poma.config import TradingMode, get_settings
from poma.data import build_data_client, utc_run_id
from poma.market_calendar import should_rebalance_now
from poma.models import OrderResult, ProposedTrade, RebalancePlan
from poma.notifications import send_alert
from poma.risk import (
    enforce_order_limits,
    enforce_turnover_limit,
    generate_trades,
    validate_targets,
)
from poma.state import LocalState
from poma.strategy import build_market_cap_targets, select_top_rank_improvements

app = typer.Typer(no_args_is_help=True)
console = Console()


def _trade_to_json(trade: ProposedTrade) -> dict[str, object]:
    payload = trade.__dict__.copy()
    payload["side"] = trade.side.value
    return payload


def _result_to_json(result: OrderResult) -> dict[str, object]:
    payload = result.__dict__.copy()
    payload["side"] = result.side.value
    return payload


def _write_report(plan: RebalancePlan, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{plan.run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": plan.run_id,
                "session_date": plan.session_date,
                "targets": [target.__dict__ for target in plan.targets],
                "trades": [_trade_to_json(trade) for trade in plan.trades],
                "execution_results": [
                    _result_to_json(result) for result in plan.execution_results
                ],
                "warnings": plan.warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return path


def _plan_status(plan: RebalancePlan, is_dry_run: bool) -> str:
    blocked = any("block execution" in warning for warning in plan.warnings)
    if is_dry_run:
        return "dry_run"
    if blocked:
        return "blocked"
    return "completed"


def _rebalance(
    session_date: str,
    run_id: str,
    force_dry_run: bool,
) -> tuple[RebalancePlan, Path]:
    settings = get_settings()
    if force_dry_run:
        settings = settings.model_copy(update={"trading_mode": TradingMode.DRY_RUN})

    data_client = build_data_client(settings)
    broker = build_broker(settings)

    current = data_client.current_universe_snapshot()
    previous = data_client.previous_universe_snapshot(settings.rank_lookback_days)
    selected = select_top_rank_improvements(
        current=current,
        previous=previous,
        max_holdings=settings.max_holdings,
    )
    targets = build_market_cap_targets(
        selected=selected,
        portfolio_value_usd=settings.portfolio_value_usd,
        cash_buffer_pct=settings.cash_buffer_pct,
        max_position_pct=settings.max_position_pct,
    )

    prices = {
        str(row.ticker): float(row.price)
        for row in current.itertuples()
        if hasattr(row, "price") and row.price is not None
    }
    positions = broker.positions()
    trades, trade_warnings = generate_trades(
        targets=targets,
        current_positions=positions,
        latest_prices=prices,
        portfolio_value_usd=settings.portfolio_value_usd,
        min_trade_notional_usd=settings.min_trade_notional_usd,
        min_weight_delta_pct=settings.min_weight_delta_pct,
        limit_offset_bps=settings.limit_offset_bps,
    )
    warnings = validate_targets(targets, settings.max_position_pct)
    warnings.extend(trade_warnings)
    warnings.extend(
        enforce_turnover_limit(
            trades,
            settings.portfolio_value_usd,
            settings.max_turnover_pct,
        )
    )
    warnings.extend(
        enforce_order_limits(
            trades,
            settings.max_order_notional_usd,
            settings.max_daily_trades,
        )
    )

    plan = RebalancePlan(
        run_id=run_id,
        session_date=session_date,
        targets=targets,
        trades=trades,
        execution_results=[],
        warnings=warnings,
    )
    report_path = _write_report(plan, settings.report_dir)

    blocked = any("block execution" in warning for warning in warnings)
    if settings.trading_mode == TradingMode.DRY_RUN or blocked:
        console.print(f"Dry run / blocked. Report written to {report_path}")
        for warning in warnings:
            console.print(f"[yellow]WARNING[/yellow] {warning}")
        send_alert(
            settings,
            f"POMA {session_date}: dry-run/blocked, {len(trades)} proposed trades",
        )
        return plan, report_path

    results = broker.submit_trades(trades)
    plan = replace(plan, execution_results=results)
    report_path = _write_report(plan, settings.report_dir)
    send_alert(settings, f"POMA {session_date}: submitted {len(trades)} trades")
    console.print(f"Submitted {len(trades)} trades. Report written to {report_path}")
    return plan, report_path


@app.command()
def rebalance(
    session_date: Annotated[
        str,
        typer.Option(help="Session label used in the report."),
    ] = "manual",
    dry_run: Annotated[
        bool,
        typer.Option(help="Force dry-run mode for this run."),
    ] = False,
) -> None:
    _rebalance(session_date=session_date, run_id=utc_run_id(), force_dry_run=dry_run)


@app.command()
def monitor(
    dry_run: Annotated[
        bool,
        typer.Option(help="Force dry-run mode for this run."),
    ] = False,
) -> None:
    settings = get_settings()
    state = LocalState(settings.state_dir)

    first_decision = should_rebalance_now(
        calendar_name=settings.market_calendar,
        after_open_minutes=settings.rebalance_after_open_minutes,
        already_ran=False,
    )
    if not first_decision.session_date:
        console.print(f"Skipping: {first_decision.reason}")
        return

    if state.has_session_attempt(first_decision.session_date):
        status = state.session_status(first_decision.session_date)
        console.print(f"Skipping: session already attempted with status={status}")
        return

    decision = should_rebalance_now(
        calendar_name=settings.market_calendar,
        after_open_minutes=settings.rebalance_after_open_minutes,
        already_ran=False,
    )
    if not decision.should_run:
        console.print(f"Skipping: {decision.reason}")
        return

    run_id = utc_run_id()
    state.begin_session(first_decision.session_date, run_id)
    try:
        plan, report_path = _rebalance(
            session_date=first_decision.session_date,
            run_id=run_id,
            force_dry_run=dry_run,
        )
        status = _plan_status(plan, settings.trading_mode == TradingMode.DRY_RUN or dry_run)
        state.mark_session(
            first_decision.session_date,
            run_id,
            status,
            report_path=str(report_path),
        )
    except Exception as exc:
        state.mark_session(first_decision.session_date, run_id, "failed", error=str(exc))
        send_alert(settings, f"POMA {first_decision.session_date}: failed: {exc}")
        raise


if __name__ == "__main__":
    app()
