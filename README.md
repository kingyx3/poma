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

IBKR execution for normal retail accounts usually requires an authenticated long-running IB Gateway, TWS, or Client Portal Gateway session. This repo therefore keeps all compute serverless-friendly while isolating the unavoidable broker gateway to a tiny VPS.

## Repository layout

```text
.
├── src/poma/                  # Application code
├── tests/                     # Unit tests
├── infra/terraform/gcp/       # GCP Cloud Run Job/Scheduler scaffold
├── ops/systemd/               # VPS service examples
├── .github/workflows/         # CI and deployment workflows
├── docs/                      # Production runbooks and configuration
├── Dockerfile                 # Cloud Run Job / executor image
├── docker-compose.vps.yml     # VPS executor deployment
└── .env.example               # Local config template
```

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

## Core safeguards included

- Dry-run default.
- Explicit live-trading guard.
- Max single-position cap.
- Cash buffer.
- Max turnover guard.
- Minimum trade notional.
- Duplicate-run idempotency key.
- Rebalance report artifact.
- Unit tests for ranking, weighting, and risk controls.

## Required GitHub configuration

See [`docs/configuration.md`](docs/configuration.md) for all required GitHub Actions secrets and variables before production deployment.

## Production readiness checklist

See [`docs/production-readiness.md`](docs/production-readiness.md).
