# POMA — Multi-strategy Portfolio Manager

POMA is a low-cost Python scaffold for a personal long-only US equities portfolio runner. It separates application architecture, portfolio capital management, broker execution, and pluggable strategy sleeves so new strategies can be added without changing the rebalance engine.

## Documentation map

| Area | Start here |
|---|---|
| App architecture and runtime flow | [`docs/architecture.md`](docs/architecture.md) |
| Portfolio sizing, allocations, cash sleeves, and portfolio-level targets | [`docs/portfolio-management.md`](docs/portfolio-management.md) |
| Strategy extension contract | [`docs/strategy-contract.md`](docs/strategy-contract.md) |
| Current built-in strategy behavior | [`docs/strategies/rank-velocity-size-equal-weight.md`](docs/strategies/rank-velocity-size-equal-weight.md) |
| Runtime configuration and GitHub secret shapes | [`docs/configuration.md`](docs/configuration.md) |
| GCP free-tier deployment | [`docs/deployment-gcp-free-tier.md`](docs/deployment-gcp-free-tier.md) |
| IB Gateway operations | [`docs/ibkr-gateway-operations.md`](docs/ibkr-gateway-operations.md) |
| Day-to-day operations and troubleshooting | [`docs/operations-runbook.md`](docs/operations-runbook.md) |
| Paper/live readiness gates | [`docs/production-readiness.md`](docs/production-readiness.md) |

## Portfolio model

```text
paper/live broker AccountSnapshot
  -> USD-only cash + USD positions + USD net liquidation
  -> MANAGED_CAP_MODE resolves managed portfolio value
  -> STRATEGY_ALLOCATIONS splits value across named sleeves
  -> each registered non-cash strategy builds a StrategyTargetBook
  -> portfolio_constructor combines sleeve targets into one target per ticker
  -> risk, order, pricing, and broker execution guards run at portfolio level
```

Capital is allocated through `STRATEGY_ALLOCATIONS`. Before each paper/live rebalance, POMA reads the configured IBKR account's USD cash, USD-denominated positions, and USD net liquidation in one `AccountSnapshot`, then resolves the managed portfolio value through `MANAGED_CAP_MODE`. A passive `cash` sleeve reserves cash explicitly; cash is not modeled as a hidden buffer inside any active strategy. See [`docs/portfolio-management.md`](docs/portfolio-management.md) for the strategy-neutral capital model.

Every allocated non-`cash` sleeve is executed through the strategy registry. When multiple sleeves target the same ticker, POMA combines their targets into one portfolio-level order. See [`docs/strategy-contract.md`](docs/strategy-contract.md) for the extension contract and [`docs/strategies/rank-velocity-size-equal-weight.md`](docs/strategies/rank-velocity-size-equal-weight.md) for the current built-in strategy.

## Architecture

```text
Ubuntu VPS / GCP e2-micro VM
  -> cron every 5 minutes
  -> POMA checks US market calendar
  -> if market has been open for 10+ minutes and today's run has not happened
  -> reads broker USD cash + USD-denominated portfolio balances from the configured IBKR account
  -> resolves managed portfolio value and strategy sleeves
  -> refreshes/saves provider market snapshots
  -> rebalances through IB Gateway on the same host
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
| `poma refresh-market-data` | Fetch the configured provider, save current/historical snapshots under `DATA_DIR`, and prepare strategy inputs. |
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
4. Run **Build app image** for the commit you want to deploy, then run **Deploy GCP e2-micro VM** with `terraform_action=plan`, then `apply` with `deploy_app=true` and the built commit-SHA image ref.
5. Before `paper` or `live`, configure and verify Gateway using [`docs/ibkr-gateway-operations.md`](docs/ibkr-gateway-operations.md).
6. Use [`docs/operations-runbook.md`](docs/operations-runbook.md) for paper/live activation, alerts, and troubleshooting.
