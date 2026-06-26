# POMA — Simple S&P 500 Rebalancer

POMA is a low-cost Python scaffold for a personal long-only S&P 500 strategy.

## Strategy

The default strategy is explicit:

```text
Universe: S&P 500
Lookback: 90 days
Score: previous_rank - current_rank
Selection: top 100 stocks by rank improvement score
Weighting: market-cap weighted, with risk caps
```

Rank 1 is the largest company by market cap, so a positive score means the stock moved up the market-cap ranking over the 90-day window.

## Architecture

```text
Ubuntu VPS / GCP e2-micro VM
  -> cron every 5 minutes
  -> POMA checks US market calendar
  -> if market has been open for 10+ minutes and today's run has not happened
  -> rebalance directly through IB Gateway on the same host
```

No Cloud Run. No Artifact Registry. No Secret Manager. No remote executor service.

The simplest recurring infrastructure is one small host plus your data-provider plan. The optional Terraform path provisions a GCP free-tier-aligned `e2-micro` VM and pushes the runtime `.env` from GitHub Actions variables/secrets. See [`docs/deployment-gcp-free-tier.md`](docs/deployment-gcp-free-tier.md).

> This repository is engineering infrastructure, not financial advice. Keep `TRADING_MODE=dry_run` or `paper` until the strategy, data, and execution are validated.

## Local quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
python -m poma.cli monitor
pytest
```

## Commands

| Command | Purpose |
|---|---|
| `poma monitor` | Cron entrypoint: rebalances once per session when the market timing and state allow it. |
| `poma rebalance [--dry-run]` | Run a rebalance now (optionally forced to dry-run). |
| `poma positions` | Print the broker's current stock portfolio (paper/live). |
| `poma doctor` | Check config, market-data provider, and IBKR connectivity; exits non-zero on failure. |
| `poma ibkr-check` | Probe only the IBKR API handshake (ignores the data provider); used by gateway ops. |

On the VM these run through the container, e.g. `cd /opt/poma && docker compose run --rm poma doctor`.

## VPS deployment

### Manual VPS

```bash
git clone <repo-url> /opt/poma
cd /opt/poma
cp .env.example .env
# edit .env, including mandatory Telegram values
bash ops/scripts/deploy.sh
crontab ops/cron/poma.cron
```

### GCP e2-micro via GitHub Actions + Terraform

1. Add only the temporary `GCP_BOOTSTRAP_SERVICE_ACCOUNT_KEY` secret.
2. Run **Bootstrap GCP Workload Identity Federation** with `terraform_action=plan`, then `apply`.
3. Delete the bootstrap key and add the runtime secrets from [`docs/deployment-gcp-free-tier.md`](docs/deployment-gcp-free-tier.md).
4. Run **Deploy GCP e2-micro VM** with `terraform_action=plan`, then `apply` with `deploy_app=true`.
5. Before `paper` or `live`, configure and verify Gateway using [`docs/ibkr-gateway-operations.md`](docs/ibkr-gateway-operations.md).

The deploy workflow renders every key from `.env.example` into a VM-local `.env` file using GitHub Variables/Secrets, uploads the repo package through IAP SSH, runs a dry-run deploy smoke test, and installs the cron schedule.

Docker Compose is used as a one-shot runner from cron. Do not run the POMA container as an always-on service.

## Trading modes

| Mode | Purpose |
|---|---|
| `dry_run` | Computes targets and writes reports only. No broker connection required. |
| `paper` | Connects to IB Gateway paper trading. |
| `live` | Connects to live IBKR. Requires `ALLOW_LIVE_TRADING=true`. |

## Included safeguards

- Dry-run default.
- Mandatory Telegram configuration at startup.
- Explicit live-trading guard.
- One attempted rebalance per market session via local state file.
- Failed runs become manual-review events.
- Market-calendar timing instead of brittle DST cron logic.
- Cash buffer.
- Max 100 holdings by default.
- Max position, turnover, order size, and trade-count limits.
- Minimum trade notional and minimum weight-delta filters.
- JSON reports with proposed trades and execution results.
- CI/CD `.env` rendering fails if required GitHub Variables/Secrets are missing.

See [`docs/configuration.md`](docs/configuration.md), [`docs/architecture.md`](docs/architecture.md), [`docs/deployment-gcp-free-tier.md`](docs/deployment-gcp-free-tier.md), [`docs/ibkr-gateway-operations.md`](docs/ibkr-gateway-operations.md), and [`docs/production-readiness.md`](docs/production-readiness.md).
