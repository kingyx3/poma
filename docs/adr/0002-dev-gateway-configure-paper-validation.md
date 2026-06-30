# ADR 0002: Dev gateway PR checks validate configure-paper

Date: 2026-06-28

Status: Accepted

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs on pull requests that need Gateway validation. The `configure-paper` action writes IBC credentials from GitHub Environment Secrets, forces a fresh Gateway login path, handles broker auth/2FA when required, and verifies a real authenticated `ib_insync` API handshake.

This credentialed path is intentionally stronger than a helper-only restart check. It proves that dev paper credentials, IBC config rendering, Gateway startup, broker auth/2FA handling when required, API socket readiness, and the application `poma ibkr-check` handshake all work before gateway-related changes merge.

Staging pushes to `main` have a different responsibility: they deploy Terraform/app changes to the staging VM. They must not rewrite broker login credentials or run `configure-paper` or `configure-live` as a side effect of app or VM deployment.

## Decision

Pull-request `dev-configure-gateway` uses `action: configure-paper`.

The dev PR check validates the full paper Gateway path:

1. Repairs and installs the Gateway runtime helpers when the runtime sentinel is stale.
2. Writes dev IBC paper credentials from GitHub Environment Secrets.
3. Truncates stale Gateway/IBC logs used for login-stage classification.
4. Forces `ibgateway.service` through a fresh stop/start path so the new config is loaded.
5. Waits for the Gateway API socket to stabilize.
6. Verifies a real authenticated `ib_insync` API handshake through `poma ibkr-check`.

Auto CI/CD uses scoped change detection:

- App/runtime code and dependency paths run deploy only.
- Gateway-owned helper/service/workflow paths run dev Gateway Ops only.
- Shared VM foundation paths, such as Auto CI/CD routing, Terraform VM foundation, and generated deploy-environment files, intentionally run both deploy and dev Gateway validation because they can affect both the VM host and Gateway runtime.

Staging Auto CI/CD has no `stg-configure-gateway` job. The `stg-deploy` job owns Terraform/app deployment, while Gateway configuration remains owned by the **IB Gateway Ops** workflow. The ops workflow rejects `deploy_environment=stg` with either `action=configure-paper` or `action=configure-live`, so staging cannot accidentally run a credential rewrite path.

Production release uses `action: configure-live` after the production deploy.

## Consequences

- Pull-request Auto CI/CD catches Gateway runtime, service, broker-login, broker auth/2FA handling, and authenticated API regressions before merge in dev.
- App/runtime-only changes do not run Gateway Ops.
- Gateway-only changes do not redeploy the app/VM.
- Shared VM foundation changes keep the safety coupling and run both deploy and dev Gateway validation when configured.
- Staging pushes to `main` are faster and safer because they stop after VM/app deployment and do not reconfigure broker login credentials.
- A configure run that reaches an API socket but cannot pass the authenticated handshake fails instead of producing a false-positive success from a stale or non-trading Gateway session.
- A timeout can mean Gateway never reached login progress, the operator did not approve broker auth in time, or the authenticated handshake failed, so diagnostics must clearly distinguish no-login-progress, socket readiness, and authenticated-handshake failures.
- Production remains gated by release promotion and the live configure path.

## Rejected alternatives

### Use only `restart` on PRs

A restart-only PR check is deterministic and does not require broker authentication, but it can miss credential rendering, IBC login automation, and authenticated API handshake regressions until after merge.

### Let staging run configure actions after every deploy

This couples VM/app deployment with credentialed Gateway mutation. It can rewrite broker credentials during ordinary staging pushes, makes staging deploys slower, and blurs ownership between deploy and Gateway operations.

### Skip gateway validation on PRs

Skipping dev gateway validation would allow helper-install, service-rendering, broker-login, and API readiness regressions to reach `main`.
