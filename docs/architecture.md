# Architecture

## Chosen architecture

```text
Ubuntu host
  -> cron every 5 minutes
  -> POMA monitor command
  -> modular market-data provider
  -> local snapshot store
  -> IB Gateway on same host
  -> IBKR
```

The host can be any small VPS. The included Terraform path provisions a GCP free-tier-aligned `e2-micro` VM for this same one-host design.

The app checks the Nasdaq/NYSE market calendar on every run and only rebalances when:

1. Today is a US trading day.
2. The market has been open for at least `REBALANCE_AFTER_OPEN_MINUTES`.
3. The local state file says today's rebalance has not already completed.

This avoids hardcoding Singapore or New York cron times and handles daylight saving time through the market-calendar library.

## Market data provider boundary

The strategy code does not depend directly on Yahoo, FMP, or any future vendor. Providers implement the normalized `MarketDataClient` contract and return snapshots with at least:

```text
ticker
market_cap
price
```

Optional provider fields such as `name`, `exchange`, `float_shares`, `shares_outstanding`, `source`, and `as_of` are preserved in the snapshot store when available.

Default provider:

```text
DATA_PROVIDER=yahoo
UNIVERSE=us_top_market_cap
```

Fallback/paid provider:

```text
DATA_PROVIDER=fmp
UNIVERSE=sp500 or nasdaq100
```

Future providers should be added as a new `MarketDataClient` implementation and registered in `build_data_client()`; engine and strategy code should not change.

## GCP e2-micro deployment path

```text
GitHub Actions
  -> render .env from GitHub Variables/Secrets
  -> Terraform apply for one GCP e2-micro VM
  -> upload repo package + .env over IAP SSH
  -> run Docker Compose dry-run smoke test
  -> install cron
```

Terraform creates only:

- One `e2-micro` VM.
- One standard persistent boot disk, capped at 30 GB.
- One dedicated VPC/subnet.
- One SSH firewall rule limited to the IAP TCP forwarding range.

The deploy path intentionally does not use Artifact Registry, Secret Manager, Cloud Run, Cloud Scheduler, Pub/Sub, Cloud NAT, Redis, or a managed database.

## Why this is simpler and cheaper

The only always-on component is the host you already need for IB Gateway. Docker images are built locally on the host, `.env` is a local file created from GitHub Actions, and cron remains the scheduler.

Removed or avoided by design:

- Cloud Run
- Cloud Scheduler
- Artifact Registry
- Secret Manager
- Remote executor API
- Multi-service deployment machinery

This eliminates the main sources of accidental cloud bill growth for a personal deployment.

## Runtime files

```text
state/rebalance_state.json       # last completed trading session
data/market_snapshots/*.csv      # provider snapshots and market-cap ranks
reports/*.json                   # generated rebalance reports
.env                             # host-local secrets/config; never commit
```

## Failure modes

| Failure | Mitigation |
|---|---|
| US DST changes | Market calendar decides the rebalance window. |
| US holiday / half-day | Market calendar returns the correct session schedule. |
| Repeated cron invocations | State file allows only one rebalance per session. |
| Missing rank history | Engine falls back to current market-cap selection and writes a warning. |
| Excess turnover | Turnover guard blocks execution. |
| Accidental live trading | `ALLOW_LIVE_TRADING=true` required for live mode. |
| Missing deploy config | CI/CD `.env` rendering fails before deployment. |
| Public SSH exposure | Terraform only allows SSH through IAP TCP forwarding. |
| Cloud cost growth | One VM, no registry, no secret store, no scheduler, no managed database. |
