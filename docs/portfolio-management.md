# Portfolio management

This page documents POMA's strategy-neutral portfolio management model. Strategy selection logic belongs in strategy-specific docs, such as [`docs/strategies/rank-velocity-size-equal-weight.md`](strategies/rank-velocity-size-equal-weight.md).

## Capital source

For `paper` and `live`, POMA sizes a rebalance from one broker `AccountSnapshot` read before planning:

```text
USD cash
+ USD-denominated positions market value
+ USD net liquidation where available
= broker account value used by POMA
```

Only actual USD cash and USD-denominated positions are used for allocation gaps and trade recommendations. Non-USD cash, `BASE` totals, and non-USD position values are ignored with a plan warning. Convert non-USD balances to USD outside POMA if they should fund the strategy sleeves.

For `dry_run`, `DRY_RUN_PORTFOLIO_VALUE_USD` is the offline portfolio-value fallback because no broker account read is available.

## Managed cap

`MANAGED_CAP_MODE` resolves the broker account value into the capital POMA may manage:

| Mode | Behavior |
|---|---|
| `broker_total` | Use the full USD-only broker account value. |
| `min_of_broker_total_and_cap` | Use `min(USD-only broker account value, MANAGED_CAP_USD)`. |

Use `min_of_broker_total_and_cap` when the IBKR account holds more USD value than POMA should manage. Paper/live execution blocks if POMA cannot read a positive broker account snapshot; it does not fall back to the dry-run value for live sizing.

## Strategy sleeves

`STRATEGY_ALLOCATIONS` splits the managed value across named sleeves:

```text
STRATEGY_ALLOCATIONS=strategy_a=0.60,strategy_b=0.20,cash=0.20
```

Rules:

- The total allocation must be `<= 1.0`.
- Every non-`cash` sleeve must be registered in `src/poma/strategies/registry.py`.
- `cash` is passive and never generates trades.
- Unallocated capital is allowed when allocations sum to less than 100%; it is reported separately from the explicit cash sleeve.
- Cash buffers should be represented as a `cash` sleeve, not as hidden logic inside an active strategy.

Each allocated active sleeve receives its own sleeve capital and returns a `StrategyTargetBook` through the strategy contract.

## Portfolio-level target construction

The rebalance engine does not submit independent orders per strategy sleeve. Instead:

```text
each active sleeve -> StrategyTargetBook
all StrategyTargetBooks -> portfolio_constructor
portfolio_constructor -> one combined target per ticker
combined targets -> trade generation and risk checks
```

If two strategies both target the same ticker, POMA sums those targets into one portfolio-level target while preserving per-strategy attribution in reports. This prevents duplicate orders and keeps risk checks at the actual portfolio level.

## Existing holdings

Existing USD-denominated stock positions in the configured IBKR account are read by ticker and included in rebalance deltas. Keep unrelated/manual positions in a separate account, or avoid overlapping tickers, if they should not affect POMA's calculations.

## Trade and risk controls

Portfolio-level trade generation and validation apply after strategy targets are combined. Important guards include:

- position concentration via `MAX_POSITION_PCT`;
- turnover via `MAX_TURNOVER_PCT`;
- minimum trade notional and minimum weight delta thresholds;
- maximum order notional and daily trade count;
- optional transaction-cost estimates;
- buying-power checks against broker cash refreshed after the sell phase;
- execution-price freshness, spread, and delayed-quote checks before paper/live submission.

Strategy docs should describe how targets are selected. Portfolio docs should describe how those targets are funded, combined, guarded, executed, and audited.

## Reporting and auditability

Reports and execution journals include the full capital picture:

- broker account snapshot;
- resolved managed portfolio value;
- per-sleeve capital allocation;
- explicit cash sleeve amount;
- unallocated capital;
- per-strategy target attribution;
- combined portfolio-level target per ticker;
- planning and execution reference prices;
- order lifecycle state and reconciliation results.

Use these fields to reconcile what each strategy wanted, what the combined portfolio intended to trade, and what IBKR ultimately accepted, filled, cancelled, or rejected.
