# Strategy contract

This page documents the extension contract for strategy sleeves. It intentionally stays strategy-neutral. Built-in strategy behavior belongs in strategy-specific pages under [`docs/strategies/`](strategies/).

## Registration

Every strategy sleeve named in `STRATEGY_ALLOCATIONS`, other than `cash`, must be registered in `src/poma/strategies/registry.py`. An unregistered name fails config validation at startup.

The passive `cash` sleeve is reserved by the portfolio allocation layer and is never executed as an active strategy.

## Runtime contract

For each allocated non-cash sleeve, the engine:

1. resolves that sleeve's capital from the managed portfolio value;
2. loads the registered strategy implementation;
3. passes normalized market/account inputs to the strategy;
4. receives a `StrategyTargetBook` from the strategy;
5. combines all `StrategyTargetBook` objects into portfolio-level targets with `src/poma/portfolio_constructor.py`.

A strategy should decide which tickers it wants and at what target weights/notional inside its sleeve. It should not independently submit orders, reserve hidden cash, or bypass portfolio-level risk controls.

## Adding a strategy

To add a strategy:

1. Implement the `Strategy` protocol in `src/poma/strategies/`.
2. Register the strategy id in `src/poma/strategies/registry.py`.
3. Add strategy-specific documentation under `docs/strategies/`.
4. Add focused tests for selection, weighting, edge cases, and registry validation.
5. Add the strategy id to `STRATEGY_ALLOCATIONS` when ready to allocate capital to it.

No engine changes should be required for a normal new strategy sleeve.

## Current built-in strategies

| Strategy id | Documentation |
|---|---|
| `rank_velocity_size_equal_weight` | [`docs/strategies/rank-velocity-size-equal-weight.md`](strategies/rank-velocity-size-equal-weight.md) |
