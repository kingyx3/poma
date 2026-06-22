# POMA — Nasdaq-100 Long-Only Rebalancer

POMA is a production-oriented Python scaffold for a low-cost, scheduled, long-only Nasdaq-100 trading strategy.

The default strategy:

1. Builds the Nasdaq-100 universe from a configured data provider.
2. Ranks companies by market cap.
3. Selects stocks whose market-cap rank is maintained or improved versus the selected lookback period.
4. Builds market-cap-weighted target positions.
5. Applies risk controls.
6. Generates a dry-run rebalance report or sends orders through an execution adapter.

> This repository is engineering infrastructure, not financial advice. Keep `TRADING_MODE=dry_run` or `paper` until you have validated data quality, taxes, costs, and execution behavior.

## Recommended production architecture

```text
Cloud Scheduler
  -> Cloud Run Job: strategy + target generation
  -> Executor API on tiny VPS
  -> IB Gateway
  -> IBKR
```

IBKR execution for normal retail accounts usually requires an authenticated long-running IB Gateway, TWS, or Client Portal Gateway session. This repo keeps all compute serverless-friendly while isolating the unavoidable broker gateway to a tiny VPS.

## Local quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python -m poma.cli rebalance --dry-run
pytest
```

## Trading modes

| Mode | Purpose |
|---|---|
| `dry_run` | Computes targets and writes reports only. No broker connection required. |
| `paper` | Sends orders to paper account/executor only. |
| `live` | Sends live orders. Requires explicit `ALLOW_LIVE_TRADING=true`. |

## Included

- Strategy, risk, broker, and data-provider abstractions.
- Dry-run default and explicit live-trading guard.
- Max position, turnover, cash-buffer, and minimum-trade safeguards.
- Docker image for Cloud Run Jobs / VPS usage.
- GitHub Actions CI and GCP deployment workflow.
- Terraform scaffold for Artifact Registry, Cloud Run Job, Scheduler, IAM, and secrets.
- Production configuration and readiness docs.

See [`docs/configuration.md`](docs/configuration.md) and [`docs/production-readiness.md`](docs/production-readiness.md).
