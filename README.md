# POMA — US Top-500 Dual-Score Rebalancer

POMA is a low-cost Python scaffold for a personal long-only US large-cap strategy portfolio.

## Strategy

```text
Portfolio value: one broker AccountSnapshot (USD cash + USD positions + USD net liquidation) before each paper/live rebalance
Managed cap: MANAGED_CAP_MODE selects broker_total (default) or min(broker total, MANAGED_CAP_USD)
Dry-run fallback: DRY_RUN_PORTFOLIO_VALUE_USD for offline planning
Current allocation: 98% to rank_velocity_size_equal_weight, 2% to cash
Universe: Yahoo Finance US top 500 by current market cap
Deduplication: one ticker per company/share-class family, preferring the most liquid class
Lookback: 90 days
Factors: market-cap size + rank-rising velocity (previous_rank - current_rank), each z-scored
Score: equal-weighted sum of the two factors (combined_score)
Selection: top 50 company stocks by combined_score (MAX_HOLDINGS, default 50)
Weighting: equal-weighted across the selected 50 names inside the 98% rank strategy sleeve, with risk caps
Cost guard: optional estimated bps/fixed transaction costs can suppress marginal trades
Sizing: whole shares only (buys round to nearest, sells round down) — the IBKR API rejects fractional order sizes
```

Rank 1 is the largest company by market cap, so a positive rank-rising velocity score means the stock moved up the market-cap ranking over the 90-day window. The size and rank-rising velocity factors are each standardized (z-scored) and summed with equal weight, so the strategy favours companies that are both large and climbing. Selected names are held at equal weight (`1/N`) inside the rank strategy sleeve, with the per-position cap still binding.

Capital is allocated through `STRATEGY_ALLOCATIONS`. Before each paper/live rebalance, POMA reads the configured IBKR account's USD cash, positions, and net liquidation in one `AccountSnapshot`, then resolves a portfolio value via `MANAGED_CAP_MODE` and uses that as the portfolio value for strategy sleeves. The engine executes every allocated non-`cash` sleeve through a strategy registry, not just one — today that is `rank_velocity_size_equal_weight=0.98`, with the passive cash sleeve `cash=0.02`. The cash sleeve counts toward the 100% portfolio value but does not generate trades. When multiple sleeves target the same ticker, POMA combines their targets into one portfolio-level order. Future strategies can be added as separate sleeves (registered in `src/poma/strategies/registry.py`), and the sum of all strategy allocations must stay at or below 100%. Cash is not modeled as a hidden buffer inside any strategy; reserve cash by allocating a `cash` sleeve. `DRY_RUN_PORTFOLIO_VALUE_USD` remains only the dry-run/offline fallback.

Only actual USD cash and USD-denominated positions are used for paper/live allocation gaps and trade recommendations. Non-USD cash, `BASE` totals, and non-USD position values are ignored with a plan warning; convert them to USD outside POMA if they should fund this strategy. Optional transaction-cost estimates can skip trades whose notional no longer clears `MIN_TRADE_NOTIONAL_USD` after expected commissions, spreads, fees, FX costs, taxes, or other trade friction.

The production market-data provider is `DATA_PROVIDER=yahoo`. It requests the largest 500 US-listed equities by current market cap, normalizes the feed into the provider contract, deduplicates share classes at issuer level when issuer/name metadata is present, and falls back to exact market-cap bucket dedupe when issuer metadata is unavailable. Future providers should implement the normalized `current_universe_snapshot()` contract without changing strategy or engine code.

Yahoo's free feed does not provide a clean bulk historical market-cap endpoint. POMA stores daily point-in-time snapshots under `DATA_DIR/market_snapshots/` and can backfill estimated historical market caps from Yahoo close prices using current share-count information.

## Architecture

```text
Ubuntu VPS / GCP e2-micro VM
  -> cron every 5 minutes
  -> POMA checks US market calendar
  -> if market has been open for 10+ minutes and today's run has not happened
  -> reads broker USD cash + USD-denominated portfolio balances from the configured IBKR account
  -> computes the active strategy sleeve from broker portfolio value * allocation_pct
  -> refreshes/saves market snapshot and rebalances through IB Gateway on the same host
```

The optional Terraform path provisions a GCP free-tier-aligned `e2-micro` VM and pushes the runtime `.env` from GitHub Actions variables/secrets. The deploy workflow renders `.env`, validates runtime config before Terraform/app deploy, runs a VM smoke test, and sends Telegram deploy status. See [`docs/deployment-gcp-free-tier.md`](docs/deployment-gcp-free-tier.md) and [`docs/operations-runbook.md`](docs/operations-runbook.md).

## Local quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
poma refresh-market-data
python -m poma.cli monitor
pytest
```

## Commands

| Command | Purpose |
|---|---|
| `poma refresh-market-data` | Fetch the configured provider, save current/historical snapshots under `DATA_DIR`, and prepare rank-history inputs. |
| `poma monitor` | Cron entrypoint: rebalances once per session when the market timing and state allow it. |
| `poma rebalance [--dry-run]` | Run a rebalance now. |
| `poma reconcile-orders` | Poll IBKR for open POMA orders and apply the replace-once/cancel timeout policy; run on a schedule to follow up on working limit orders after the rebalance process exits. |
| `poma positions` | Print the broker's current stock portfolio. |
| `poma doctor` | Check config, market-data provider, and IBKR connectivity. |
| `poma ibkr-check` | Probe only the IBKR API handshake. |

## GCP e2-micro via GitHub Actions + Terraform

1. Add only the temporary `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` secret.
2. Run **Bootstrap GCP Workload Identity Federation** with `terraform_action=plan`, then `apply`.
3. Delete the bootstrap key and add the runtime secrets from [`docs/configuration.md`](docs/configuration.md).
4. Run **Deploy GCP e2-micro VM** with `terraform_action=plan`, then `apply` with `deploy_app=true`.
5. Before `paper` or `live`, configure and verify Gateway using [`docs/ibkr-gateway-operations.md`](docs/ibkr-gateway-operations.md).
6. Use [`docs/operations-runbook.md`](docs/operations-runbook.md) for paper/live activation, alerts, and troubleshooting.

See [`docs/configuration.md`](docs/configuration.md), [`docs/architecture.md`](docs/architecture.md), [`docs/deployment-gcp-free-tier.md`](docs/deployment-gcp-free-tier.md), [`docs/ibkr-gateway-operations.md`](docs/ibkr-gateway-operations.md), [`docs/operations-runbook.md`](docs/operations-runbook.md), and [`docs/production-readiness.md`](docs/production-readiness.md).
