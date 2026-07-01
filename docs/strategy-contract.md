# Strategy contract

Every strategy sleeve named in `STRATEGY_ALLOCATIONS` (other than `cash`) must be registered in `src/poma/strategies/registry.py`; an unregistered name fails config validation at startup. The engine builds one `StrategyTargetBook` per registered, allocated sleeve and combines them into a single portfolio-level target per ticker (`src/poma/portfolio_constructor.py`). Adding a new strategy means implementing the `Strategy` protocol in `src/poma/strategies/` (see `rank_velocity_size_equal_weight.py`) and registering it — no engine changes required.

Production behavior of the current `rank_velocity_size_equal_weight` strategy:

- Use the largest 500 US-listed companies by current market cap from the configured provider.
- Normalize provider rows into the internal snapshot contract.
- Collapse multiple share classes of the same company before ranking.
- Compute a dual score from current size and 90-day rank-rising velocity.
- Select the top 100 company stocks by that combined score.
- Equal-weight the selected names, subject to the configured risk caps.

This is a US top-market-cap strategy, not a strict index-constituent strategy.
