# POMA — Simple Nasdaq-100 Rebalancer

POMA is a low-cost Python trading bot for a personal long-only Nasdaq-100 strategy.

It is intentionally simple:

```text
Ubuntu VPS
  -> cron every 5 minutes
  -> POMA checks US market calendar
  -> if market has been open for 10+ minutes and today's run has not happened
  -> rebalance directly through IB Gateway on the same VPS
```

No Cloud Run. No Terraform. No Artifact Registry. No Secret Manager. No remote executor service.

That avoids silent cloud-cost creep. The expected recurring infra cost is just the VPS plus your data-provider plan.

> This repository is engineering infrastructure, not financial advice. Keep `TRADING_MODE=dry_run` or `paper` until the strategy, data, and execution are validated.

## Strategy

1. Fetch Nasdaq-100 constituents and market caps.
2. Rank by market cap.
3. Select stocks whose market-cap rank is maintained or improved versus the lookback snapshot.
4. Weight selected names by market cap.
5. Apply risk controls.
6. Trade only when the target/current weight delta clears the configured threshold.

Default timing is **10 minutes after US market open**, checked using a real exchange calendar so daylight saving time, US holidays, and half-days are handled by the application rather than by cron.

## Local quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python -m poma.cli monitor
pytest
```

## VPS deployment

```bash
git clone <repo-url> /opt/poma
cd /opt/poma
cp .env.example .env
# edit .env
docker compose build
docker compose up -d
```

Install the sample cron so the container checks every 5 minutes:

```bash
crontab ops/cron/poma.cron
```

## Trading modes

| Mode | Purpose |
|---|---|
| `dry_run` | Computes targets and writes reports only. No broker connection required. |
| `paper` | Connects to IB Gateway paper trading. |
| `live` | Connects to live IBKR. Requires `ALLOW_LIVE_TRADING=true`. |

## Included safeguards

- Dry-run default.
- Explicit live-trading guard.
- One rebalance per market session via local state file.
- Market-calendar timing instead of brittle DST cron logic.
- Cash buffer.
- Max single-position cap.
- Max turnover block.
- Minimum trade notional.
- Minimum target/current weight delta.
- JSON rebalance reports.
- Optional Telegram alerts.

See [`docs/configuration.md`](docs/configuration.md), [`docs/architecture.md`](docs/architecture.md), and [`docs/production-readiness.md`](docs/production-readiness.md).
