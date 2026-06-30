# ADR 0002: Dev gateway PR checks validate configure-paper

Date: 2026-06-28

Status: Accepted

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs only on pull requests that touch
Gateway-owned files or shared VM/Gateway foundation files. The `configure-paper` action writes
IBC paper credentials from GitHub Environment Secrets, forces a fresh Gateway login path, and
verifies a real authenticated `ib_insync` API handshake.

This credentialed path is intentionally stronger than a helper-only restart check. It proves
that dev paper credentials, IBC config rendering, Gateway startup, API socket readiness, and
the application `poma ibkr-check` handshake all work before gateway-related changes merge.
App/runtime-only changes should not be blocked by broker-login availability when they do not
modify Gateway runtime behavior.

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

- Auto CI/CD routing, app/runtime code, and dependency paths run deploy only.
- Gateway-owned helper/service/workflow paths run dev Gateway Ops only.
- Shared VM foundation paths, such as Terraform VM foundation and generated deploy-environment
  files, intentionally run both deploy and dev Gateway validation because they can affect both the
  VM host and Gateway runtime.

Staging is not deployed or configured from Auto CI/CD. Staging and non-production Gateway lifecycle
operations are owned by the manual deploy and IB Gateway Ops workflows. Production release uses
`action: configure-live`; live configure additionally requires fresh IBKR 2FA evidence before the
authenticated API handshake is accepted.

## Consequences

- Pull-request Auto CI/CD catches Gateway runtime, service, broker-login, and authenticated API
  regressions before merge in dev when Gateway-relevant files change.
- App/runtime-only PRs are not blocked by an unnecessary credentialed Gateway login.
- Gateway-only changes do not redeploy the app/VM.
- Shared VM foundation changes keep the safety coupling and run both deploy and dev Gateway
  validation when configured.
- Main-branch pushes do not automatically deploy or configure staging from Auto CI/CD.
- A configure run that reaches an API socket but cannot pass the authenticated handshake fails
  instead of producing a false-positive success from a stale or non-trading Gateway session.
- A timeout can mean Gateway never reached login progress or the authenticated handshake failed,
  so diagnostics must clearly distinguish no-login-progress, socket readiness, and
  authenticated-handshake failures.
- Production remains gated by release promotion and the live configure path.

## Rejected alternatives

### Use only `restart` on PRs

A restart-only PR check is deterministic and does not require broker authentication, but it can miss
credential rendering, IBC login automation, and authenticated API handshake regressions until after
merge.

### Run Gateway Ops for every deploy-required PR

This couples app deployment with credentialed Gateway mutation. It can fail app-only PRs because a
broker login is unavailable, makes CI slower, and blurs ownership between deploy and Gateway
operations.

### Let staging run configure actions after every deploy

This couples staging VM/app deployment with credentialed Gateway mutation. It can rewrite broker
credentials during ordinary staging pushes, makes staging deploys slower, and blurs ownership between
deploy and Gateway operations.

### Skip gateway validation on PRs

Skipping dev gateway validation would allow helper-install, service-rendering, broker-login, and API
readiness regressions to reach `main`.
