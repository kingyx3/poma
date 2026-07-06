# Workflow execution runbook

The manual Reconcile Orders GitHub workflow has been removed. Normal order expiry and cancel state is refreshed by the scheduled app lifecycle job and by the next monitor guard before a rebalance can block on stale local order rows.
