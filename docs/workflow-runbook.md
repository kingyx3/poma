# Workflow execution runbook

This runbook shows the order for the repository workflows and the recovery path for an existing VM.

## Flowchart

```mermaid
flowchart TD
  A([Start]) --> B{Environment bootstrapped?}
  B -- No --> C[Bootstrap workflow: plan]
  C --> D[Bootstrap workflow: apply]
  D --> E[Add environment secrets]
  B -- Yes --> E
  E --> F[Deploy workflow: dry-run plan]
  F --> G[Deploy workflow: dry-run apply]
  G --> H{Existing VM repair needed?}
  H -- Yes --> I[Refresh Gateway Helper]
  H -- No --> J[Gateway Ops configure]
  I --> J
  J --> K[Approve phone prompt]
  K --> L{Gateway socket reachable?}
  L -- No --> M[Gateway Ops logs status restart]
  M --> J
  L -- Yes --> N[Deploy workflow: paper mode]
  N --> O[Observe paper runs]
  O --> P[Promote only after review]
```

## Main sequence

| Step | Workflow | Use when | Result |
|---|---|---|---|
| 1 | Bootstrap GCP Workload Identity Federation: plan | First setup for an environment | Preview cloud identity setup |
| 2 | Bootstrap GCP Workload Identity Federation: apply | Bootstrap plan is acceptable | Writes generated deploy config |
| 3 | Discover Telegram chat ID | Chat id is unknown | Finds alert destination |
| 4 | Deploy GCP e2-micro VM: plan | Runtime secrets are set | Preview VM/app changes |
| 5 | Deploy GCP e2-micro VM: apply with dry run | Plan is acceptable | Creates or updates VM and app |
| 6 | Refresh Gateway Helper | Existing VM has stale helper or missing service | Repairs helper and Gateway service |
| 7 | IB Gateway Ops: configure-paper | VM is healthy and broker login secrets are set | Configures Gateway paper session |
| 8 | Deploy GCP e2-micro VM: paper | Gateway socket is reachable | Runs app against paper account |
| 9 | IB Gateway Ops: logs/status/restart/verify-socket | Maintenance or troubleshooting | Diagnoses or restarts Gateway |

## Recovery map

| Symptom | Run next |
|---|---|
| Helper command missing | Refresh Gateway Helper |
| IBC template missing | Refresh Gateway Helper |
| Gateway service unit missing | Refresh Gateway Helper |
| Socket not reachable | IB Gateway Ops logs, status, restart, verify-socket |

## Environment secrets summary

| Workflow | Secrets needed |
|---|---|
| Bootstrap | Temporary bootstrap service account key |
| Deploy | Telegram token, Telegram chat id, data provider key, account selector for selected mode, Tailscale key when joining tailnet |
| Refresh Gateway Helper | Generated GCP deploy config from bootstrap |
| IB Gateway Ops configure actions | Broker login id and broker login secret |

## Safe dev path

1. Bootstrap plan.
2. Bootstrap apply.
3. Discover Telegram chat id if needed.
4. Add dev runtime secrets.
5. Deploy dry-run plan.
6. Deploy dry-run apply.
7. Refresh Gateway Helper once for existing VMs.
8. IB Gateway Ops configure-paper.
9. Approve the phone prompt.
10. Deploy paper mode.
