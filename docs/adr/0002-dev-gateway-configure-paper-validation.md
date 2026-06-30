# ADR 0002: Dev gateway PR checks validate configure-paper

Date: 2026-06-28

Status: Accepted

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs on pull requests that need Gateway validation. The `configure-paper` action writes IBC credentials from GitHub Environment Secrets, forces a fresh Gateway login path, and verifies a real authenticated `ib_insync` API handshake.

This credentialed path is intentionally stronger than a helper-only restart check. It proves that dev paper credentials, IBC config rendering, Gateway startup, API socket readiness, and the application `poma ibkr-check` handshake all work before gateway-related changes merge.

Staging pushes to `main` have a different responsibility: they deploy Terraform/app changes to the staging VM. They must not rewrite broker login credentials or run `configure-paper` as a side effect of app or VM deployment.

## Decision

Pull-request `dev-configure-gateway` uses `action: configure-paper`.

The dev PR check validates the full paper Gateway path:

1. Repairs and installs the Gateway runtime helpers when the runtime sentinel is stale.
2. Writes dev IBC paper credentials from GitHub Environment Secrets.
3. Truncates stale Gateway/IBC logs used for login-stage classification.
4. Forces `ibgateway.service` through a fresh stop/start path so the new config is loaded.
5. Waits for the Gateway API socket to stabilize.
6. Verifies a real authenticated `ib_insync` API handshake through `poma ibkr-check`.

Staging Auto CI/CD no longer has a `stg-configure-gateway` job. The `stg-deploy` job owns only Terraform/app deployment, while Gateway configuration remains owned by the **IB Gateway Ops** workflow. The ops workflow also rejects `deploy_environment=stg` with `action=configure-paper` so staging cannot accidentally run the paper credential rewrite path.

Production release uses `action: configure-live` after the production deploy.

## Consequences

- Pull-request Auto CI/CD catches Gateway runtime, service, broker-login, and authenticated API regressions before merge in dev.
- Staging pushes to `main` are faster and safer because they stop after VM/app deployment and do not reconfigure broker login credentials.
- Deploy-relevant path detection and Gateway-relevant path detection stay separate; a VM/app change does not automatically imply a Gateway configure action.
- A configure run that reaches an API socket but cannot pass the authenticated handshake fails instead of producing a false-positive success from a stale or non-trading Gateway session.
- A timeout can mean Gateway never reached login progress, the operator did not approve broker auth in time, or the authenticated handshake failed, so diagnostics must clearly distinguish no-login-progress, socket readiness, and authenticated-handshake failures.
- Production remains gated by release promotion and the live configure path.

## Rejected alternatives

### Use only `restart` on PRs

A restart-only PR check is deterministic and does not require broker authentication, but it can miss credential rendering, IBC login automation, and authenticated API handshake regressions until after merge.

### Let staging run `configure-paper` after every deploy

This couples VM/app deployment with credentialed Gateway mutation. It can rewrite broker credentials during ordinary staging pushes, makes staging deploys slower, and blurs ownership between deploy and Gateway operations.

### Skip gateway validation on PRs

Skipping dev gateway validation would allow helper-install, service-rendering, broker-login, and API readiness regressions to reach `main`.
