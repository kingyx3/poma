# POMA — US Top-500 Dual-Score Rebalancer

POMA is a low-cost Python scaffold for a personal long-only US large-cap strategy portfolio.

## Strategy

```text
Portfolio size: broker USD cash + current stock market value for paper/live; PORTFOLIO_VALUE_USD for dry-run fallback
Current allocation: 98% to rank_velocity_size_equal_weight, 2% to cash
Universe: Yahoo Finance US top 500 by current market cap
Deduplication: one ticker per company/share-class family, preferring the most liquid class
Lookback: 90 days
Factors: market-cap size + rank-rising velocity (previous_rank - current_rank), each z-scored
Score: equal-weighted sum of the two factors (combined_score)
Selection: top 100 company stocks by combined_score
Weighting: equal-weighted across the selected 100 names inside the 98% rank strategy sleeve, with risk caps
```

Rank 1 is the largest company by market cap, so a positive rank-rising velocity score means the stock moved up the market-cap ranking over the 90-day window. The size and rank-rising velocity factors are each standardized (z-scored) and summed with equal weight, so the strategy favours companies that are both large and climbing. Selected names are held at equal weight (`1/N`) inside the rank strategy sleeve, with the per-position cap still binding.

Capital is allocated through `STRATEGY_ALLOCATIONS`. Today the active trading strategy is `rank_velocity_size_equal_weight=0.98`, and the passive cash sleeve is `cash=0.02`. The cash sleeve counts toward the 100% portfolio size but does not generate trades. Future strategies can be added as separate sleeves, and the sum of all strategy allocations must stay at or below 100%. In paper/live mode, the portfolio size is derived from the configured broker account before each rebalance; `PORTFOLIO_VALUE_USD` is only the dry-run fallback. Cash is not modeled as a hidden buffer inside an active strategy; reserve cash by allocating a `cash` sleeve.

The production market-data provider is `DATA_PROVIDER=yahoo`. It requests the largest 500 US-listed equities by current market cap, normalizes the feed into the provider contract, deduplicates share classes at issuer level when issuer/name metadata is present, and falls back to exact market-cap bucket dedupe when issuer metadata is unavailable. Future providers should implement the normalized `current_universe_snapshot()` contract without changing strategy or engine code.

Yahoo's free feed does not provide a clean bulk historical market-cap endpoint. POMA stores daily point-in-time snapshots under `DATA_DIR/market_snapshots/` and can backfill estimated historical market caps from Yahoo close prices using current share-count information.

## Architecture

```text
Ubuntu VPS / GCP e2-micro VM
  -> cron every 5 minutes
  -> POMA checks US market calendar
  -> if market has been open for 10+ minutes and today's run has not happened
  -> paper/live: reads broker USD cash + current stock market value
  -> computes the active strategy sleeve from portfolio_value_usd * allocation_pct
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
