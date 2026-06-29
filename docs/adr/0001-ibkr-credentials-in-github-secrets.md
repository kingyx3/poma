# ADR 0001: Store IBKR Gateway login credentials in GitHub Environment Secrets

Date: 2026-06-26

Status: Accepted

## Context

POMA configures IB Gateway through the manual **IB Gateway Ops** GitHub Actions workflow. The workflow connects to the VM over IAP SSH and invokes `sudo poma-configure-ibc` to write the VM-local IBC config used by `ibgateway.service`.

Earlier documentation preferred entering broker login credentials directly on the VM. The intended operating model is now to keep the IBKR login credentials in GitHub Environment Secrets so paper/live Gateway configuration is repeatable, auditable, and aligned with the rest of the GitHub Actions deployment path.

Paper and live IBKR credentials can be different. Dev/stg use paper Gateway configuration alongside `IBKR_ACCOUNT_PAPER`, so paper login credentials must not be forced to share the live `IBKR_LOGIN_ID` / `IBKR_LOGIN_SECRET` pair.

The project intentionally avoids GCP Secret Manager, Terraform-managed broker secrets, VM metadata secrets, and committed `.env` credentials to keep the one-host deployment simple and low cost.

## Decision

Store paper IBKR Gateway login credentials as GitHub Environment Secrets:

- `IBKR_LOGIN_ID_PAPER` — the IBKR paper Gateway login username.
- `IBKR_LOGIN_SECRET_PAPER` — the IBKR paper Gateway login password.

Store live IBKR Gateway login credentials as separate GitHub Environment Secrets:

- `IBKR_LOGIN_ID` — the IBKR live Gateway login username.
- `IBKR_LOGIN_SECRET` — the IBKR live Gateway login password.

Use the paper pair only in the explicit `configure-paper` action of the **IB Gateway Ops** workflow. Use the live pair only in the explicit `configure-live` action. The workflow passes the selected credentials to `sudo poma-configure-ibc` over SSH stdin and does not place either pair in the app `.env`.

IBKR account identifiers remain separate from login credentials:

- `IBKR_ACCOUNT_PAPER` is used by paper deploy rendering.
- `IBKR_ACCOUNT` is used by live deploy rendering.

## Consequences

- Dev/stg paper Gateway setup can use `IBKR_LOGIN_ID_PAPER` / `IBKR_LOGIN_SECRET_PAPER` alongside `IBKR_ACCOUNT_PAPER` without depending on live login secrets.
- Broker login credential access is governed by GitHub Environment Secret access and any configured environment protection rules.
- Operators can reconfigure paper/live Gateway without manually typing credentials into the VM.
- GitHub Actions logs must never echo the credential values.
- The credentials must not be written to Terraform variables, Terraform state, VM metadata, the app `.env`, or repository files.
- Rotating paper credentials means updating `IBKR_LOGIN_ID_PAPER` / `IBKR_LOGIN_SECRET_PAPER` and rerunning `configure-paper`; rotating live credentials means updating `IBKR_LOGIN_ID` / `IBKR_LOGIN_SECRET` and rerunning `configure-live`.
- Runtime IBC configuration still resides on the VM after configuration because IB Gateway needs local credentials to launch under `ibgateway.service`.
- Mobile approval, session reset, and IBKR security prompts can still require manual operator action; this decision does not bypass broker authentication requirements.

## Rejected alternatives

### VM-only manual entry

This avoids storing broker login credentials in GitHub, but it makes paper/live setup less repeatable and leaves the deployment path dependent on manual SSH terminal input.

### GCP Secret Manager

This centralizes secret storage in GCP, but it adds another paid/managed service and conflicts with the project's current low-cost one-host architecture.

### Terraform variables or VM metadata

This is rejected because it increases the risk of broker secrets entering Terraform state, plan output, cloud metadata, or long-lived infrastructure records.
