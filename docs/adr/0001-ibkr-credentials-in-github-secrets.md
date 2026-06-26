# ADR 0001: Store IBKR Gateway login credentials in GitHub Environment Secrets

Date: 2026-06-26

Status: Accepted

## Context

POMA configures IB Gateway through the manual **IB Gateway Ops** GitHub Actions workflow. The workflow connects to the VM over IAP SSH and invokes `sudo poma-configure-ibc` to write the VM-local IBC config used by `ibgateway.service`.

Earlier documentation preferred entering broker login credentials directly on the VM. The intended operating model is now to keep the IBKR login credentials in GitHub Environment Secrets so paper/live Gateway configuration is repeatable, auditable, and aligned with the rest of the GitHub Actions deployment path.

The project intentionally avoids GCP Secret Manager, Terraform-managed broker secrets, VM metadata secrets, and committed `.env` credentials to keep the one-host deployment simple and low cost.

## Decision

Store the IBKR Gateway login credentials as GitHub Environment Secrets:

- `IBKR_LOGIN_ID` — the IBKR Gateway login username.
- `IBKR_LOGIN_SECRET` — the IBKR Gateway login password.

Use these secrets only in the explicit `configure-paper` and `configure-live` actions of the **IB Gateway Ops** workflow. The workflow passes the credentials to `sudo poma-configure-ibc` over SSH stdin, writes only a temporary runner-side input file, and removes that file at the end of the job.

IBKR account identifiers remain separate from login credentials:

- `IBKR_ACCOUNT_PAPER` is used by paper deploy rendering.
- `IBKR_ACCOUNT` is used by live deploy rendering.

## Consequences

- Broker login credential access is governed by GitHub Environment Secret access and any configured environment protection rules.
- Operators can reconfigure paper/live Gateway without manually typing credentials into the VM.
- GitHub Actions logs must never echo the credential values.
- The credentials must not be written to Terraform variables, Terraform state, VM metadata, the app `.env`, or repository files.
- Rotating IBKR login credentials means updating the GitHub Environment Secrets and rerunning the appropriate Gateway configure action.
- Runtime IBC configuration still resides on the VM after configuration because IB Gateway needs local credentials to launch under `ibgateway.service`.
- Mobile approval, session reset, and IBKR security prompts can still require manual operator action; this decision does not bypass broker authentication requirements.

## Rejected alternatives

### VM-only manual entry

This avoids storing broker login credentials in GitHub, but it makes paper/live setup less repeatable and leaves the deployment path dependent on manual SSH terminal input.

### GCP Secret Manager

This centralizes secret storage in GCP, but it adds another paid/managed service and conflicts with the project's current low-cost one-host architecture.

### Terraform variables or VM metadata

This is rejected because it increases the risk of broker secrets entering Terraform state, plan output, cloud metadata, or long-lived infrastructure records.
