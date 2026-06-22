# POMA — Simple Nasdaq-100 Rebalancer

POMA is a low-cost Python scaffold for a personal long-only Nasdaq-100 strategy.

## Strategy

The default strategy is now explicit:

```text
Universe: Nasdaq-100
Lookback: 90 days
Score: previous_rank - current_rank
Selection: top 30 stocks by rank improvement score
Weighting: market-cap weighted, with risk caps
```

Rank 1 is the largest company by market cap, so a positive score means the stock moved up the market-cap ranking over the 90-day window.

## Architecture

```text
Ubuntu VPS
  -> cron every 5 minutes
  -> POMA checks US market calendar
  -> if market has been open for 10+ minutes and today's run has not happened
  -> rebalance directly through IB Gateway on the same VPS
```

No Cloud Run. No Terraform. No Artifact Registry. No Secret Manager. No remote executor service.

The expected recurring infrastructure cost is just the VPS plus your data-provider plan.

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

## VPS deployment

```bash
git clone <repo-url> /opt/poma
cd /opt/poma
cp .env.example .env
# edit .env, including mandatory Telegram values
bash ops/scripts/deploy.sh
crontab ops/cron/poma.cron
```

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
- Max 30 holdings by default.
- Max position, turnover, order size, and trade-count limits.
- Minimum trade notional and minimum weight-delta filters.
- JSON reports with proposed trades and execution results.

See [`docs/configuration.md`](docs/configuration.md), [`docs/architecture.md`](docs/architecture.md), and [`docs/production-readiness.md`](docs/production-readiness.md).
