# Architecture

## Chosen architecture

```text
Ubuntu host
  -> cron every 5 minutes
  -> POMA monitor command
  -> Yahoo/yfinance market data
  -> local snapshot store
  -> IB Gateway on same host
  -> IBKR
```

The host can be any small VPS. The included Terraform path provisions a GCP free-tier-aligned `e2-micro` VM for this same one-host design.

The app checks the market calendar on every run and only rebalances when:

1. Today is a US trading day.
2. The market has been open for at least `REBALANCE_AFTER_OPEN_MINUTES`.
3. The local state file says today's rebalance has not already completed.

## Market data provider boundary

The strategy code consumes normalized snapshots and does not depend directly on the data adapter implementation. The production adapter is Yahoo/yfinance and returns snapshots with at least:

```text
ticker
market_cap
price
```

Optional provider fields such as `name`, `exchange`, `volume`, `dollar_volume`, `float_shares`, `shares_outstanding`, `source`, and `as_of` are preserved when available.

Default provider:

```text
DATA_PROVIDER=yahoo
UNIVERSE=us_top_market_cap
```

`DATA_PROVIDER=fixture` remains available for tests and PR dry-runs. Future providers should be added as a new `MarketDataClient` implementation and registered in `build_data_client()`; engine and strategy code should not change.

## GCP e2-micro deployment path

```text
GitHub Actions
  -> render .env from GitHub Variables/Secrets
  -> Terraform apply for one GCP e2-micro VM
  -> upload repo package + .env over IAP SSH
  -> run Docker Compose dry-run smoke test
  -> install cron
```

Terraform creates one small VM, one standard boot disk, one dedicated VPC/subnet, and one SSH firewall rule limited to the IAP TCP forwarding range.

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
