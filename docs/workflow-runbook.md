# Workflow execution runbook

This runbook shows the normal order for repository workflows and recovery checks for an existing VM.

## Main sequence

| Step | Workflow | Use when | Result |
|---|---|---|---|
| 1 | Bootstrap GCP Workload Identity Federation: plan | First setup for an environment | Preview cloud identity setup |
| 2 | Bootstrap GCP Workload Identity Federation: apply | Bootstrap plan is acceptable | Writes generated deploy config |
| 3 | Discover Telegram chat ID | Chat id is unknown | Finds alert destination |
| 4 | Deploy GCP e2-micro VM: plan | Runtime settings are ready | Preview VM and app changes |
| 5 | Deploy GCP e2-micro VM: apply with dry run | Plan is acceptable | Creates or updates VM and app |
| 6 | IB Gateway Ops: restart | Existing VM has stale helper or missing service | Repairs helpers and restarts the Gateway service |
| 7 | IB Gateway Ops: configure-paper | VM is healthy and paper settings are ready | Configures the paper session |
| 8 | Deploy GCP e2-micro VM: paper | Gateway socket is reachable | Runs the app against the paper environment |
| 9 | IB Gateway Ops: logs/status/restart/verify-socket | Maintenance or troubleshooting | Diagnoses or restarts Gateway |
| 10 | Deploy GCP e2-micro VM: undeploy plan, then undeploy apply | Retiring one selected environment VM | Removes the selected VM foundation |

## Recovery map

| Symptom | Run next |
|---|---|
| Helper command missing | IB Gateway Ops restart |
| IBC template missing | IB Gateway Ops restart |
| Gateway service unit missing | IB Gateway Ops restart |
| Socket not reachable | IB Gateway Ops logs, status, restart, verify-socket |
| Rebalance blocked by unresolved open orders from another run/session | Review the blocked report and broker activity. Normal expiry/cancel state is refreshed automatically by cron and by the next monitor guard before the block is decided. |
| Selected environment VM should be removed | Deploy GCP e2-micro VM with `deployment_action=undeploy`, first `terraform_action=plan`, then `apply` |

## Order lifecycle recovery

The manual Reconcile Orders GitHub workflow has been removed. Accepted limit orders are followed up by the scheduled app lifecycle job on the VM. If an order times out and POMA requests a cancel, the next lifecycle poll or monitor pre-check updates the local ledger from broker open-order state before allowing a stale local row to block a rebalance.

## Safe dev path

1. Bootstrap plan.
2. Bootstrap apply.
3. Discover Telegram chat id if needed.
4. Add dev runtime settings.
5. Deploy dry-run plan.
6. Deploy dry-run apply.
7. Run IB Gateway Ops `restart` once for existing VMs that need helper/service repair.
8. Pull-request Auto CI/CD runs IB Gateway Ops `configure-paper` for dev paper validation.
9. Deploy paper mode.
