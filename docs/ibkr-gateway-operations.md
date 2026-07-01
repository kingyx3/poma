# IB Gateway operations

POMA runs IB Gateway on the same GCP e2-micro VM as the bot. The goal is one cheap host, supervised Gateway, and broker login credentials supplied from GitHub Environment Secrets only during explicit Gateway configure actions.

See [`adr/0001-ibkr-credentials-in-github-secrets.md`](adr/0001-ibkr-credentials-in-github-secrets.md) for the credential-storage decision.

## Production flow

Use this flow for manual paper/live setup and for production promotion. Auto CI/CD also invokes Gateway Ops automatically for dev pull requests and staging pushes when deploy or Gateway paths changed.

1. Deploy the VM using [`deployment-gcp-free-tier.md`](deployment-gcp-free-tier.md).
2. Add the required GitHub Environment Secrets for the target environment:

```text
# configure-paper, used by dev/stg paper environments alongside IBKR_ACCOUNT_PAPER
IBKR_LOGIN_ID_PAPER=<ibkr-paper-gateway-login-username>
IBKR_LOGIN_SECRET_PAPER=<ibkr-paper-gateway-login-password>

# configure-live, used only for live Gateway configuration
IBKR_LOGIN_ID=<ibkr-live-gateway-login-username>
IBKR_LOGIN_SECRET=<ibkr-live-gateway-login-password>
```

3. Run **IB Gateway Ops** with `action=configure-paper` before paper mode, or `action=configure-live` before live mode.
4. For paper, the workflow proceeds to API readiness after Gateway restart. For live, approve broker mobile authentication when prompted.
5. Verify Gateway before paper/live mode:

```bash
sudo systemctl status ibgateway --no-pager
nc -z 127.0.0.1 7497 && echo "IB Gateway API socket is reachable"
```

6. Only after the socket is reachable, redeploy with `trading_mode=paper` or `trading_mode=live`.

Use direct SSH only for recovery or manual break-glass operations. IAP SSH is the access path:

```bash
gcloud compute ssh poma-<env>-free-tier --zone us-west1-b --tunnel-through-iap
```

For startup-stage diagnosis when no mobile approval prompt appears, see [`ib-gateway-startup-diagnosis.md`](ib-gateway-startup-diagnosis.md).

## What is automated

The VM startup script keeps boot light: it installs only Docker, cron, the app user, and runtime
directories. The IB Gateway runtime is installed and enabled by the **IB Gateway Ops** workflow.
Auto CI/CD runs Gateway Ops after its dev/stg deploy jobs when deploy or Gateway paths changed;
manual deploys require an explicit Gateway Ops action. Gateway Ops provisions:

- IB Gateway in `/opt/ibgateway`.
- IBC in `/opt/ibc`.
- `ibgateway.service` under `systemd`.
- A headless display and localhost-only VNC for recovery.
- `/usr/local/bin/poma-configure-ibc` for the required IBC credential setup.
- `/usr/local/bin/poma-diagnose-ibgateway` for startup diagnosis.

The **IB Gateway Ops** workflow reads `IBKR_LOGIN_ID_PAPER` and `IBKR_LOGIN_SECRET_PAPER` from GitHub Environment Secrets only for `configure-paper`. It reads `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET` only for `configure-live`. The selected pair is sent to `sudo poma-configure-ibc` over IAP SSH stdin and is not written to the app `.env`.

The same ops workflow repairs the Gateway runtime before `restart`, `verify-socket`, `configure-paper`, and `configure-live`. The repair is intentionally self-healing: it can reinstall missing headless packages, rebuild the runtime wrapper/service, install missing IB Gateway and IBC artifacts, fix stale `/tmp/poma-ibgateway` ownership, and move sidecar logs to the systemd-managed `/var/log/poma/ibgateway` directory. Pull-request Auto CI/CD uses `configure-paper` for the dev Gateway check so paper broker-login and authenticated API regressions are caught before merge. Configure and socket verification wait for two stable `127.0.0.1:7497` polls before running the real `poma ibkr-check` handshake, print the redacted handshake tail on failure, and tolerate a transient post-socket service restart until the bounded readiness deadline. Gateway Ops allows up to 600 seconds for the IBC/Gateway login path so slow first starts do not fail while Gateway is still progressing. Live configure also waits for fresh mobile-approval evidence before the authenticated API check.

The service starts raw IB Gateway until `/home/poma/ibc/config.ini` exists. After setup, it starts Gateway through IBC as one foreground systemd process and refuses to fall back to raw Gateway if the configured IBC launch path is broken. The service supervisor only treats the real Java Gateway process or API listener as meaningful startup progress, not wrapper shell commands that merely contain Gateway paths. It also pins `LoginDialogDisplayTimeout=240` before launch so slow e2-micro cold starts do not loop on IBC's default 60-second login-dialog timeout.

## Credential handling rules

- Store paper IBKR Gateway login credentials only as GitHub Environment Secrets named `IBKR_LOGIN_ID_PAPER` and `IBKR_LOGIN_SECRET_PAPER`.
- Store live IBKR Gateway login credentials only as GitHub Environment Secrets named `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET`.
- Keep Gateway login credentials separate from account selector secrets such as `IBKR_ACCOUNT_PAPER` and `IBKR_ACCOUNT`.
- Do not commit broker login credentials.
- Do not add broker login credentials to Terraform variables, Terraform state, VM metadata, GCP Secret Manager, or the app `.env`.
- Do not echo broker login credentials in workflow logs.
- Rotate credentials by updating the GitHub Environment Secrets and rerunning the relevant Gateway configure action.

## Recovery commands

```bash
sudo poma-diagnose-ibgateway validate --mode paper
sudo poma-diagnose-ibgateway startup-check --log-lines 80
sudo poma-diagnose-ibgateway diagnose --log-lines 200
sudo systemctl restart ibgateway
sudo journalctl -u ibgateway -n 200 --no-pager
sudo tail -n 120 /var/log/poma/ibgateway/*.log
```

Run **IB Gateway Ops** with `action=app-logs` to read the app-side `poma monitor`/`poma reconcile-orders` cron output (`/opt/poma/logs/poma-cron.log`, `/opt/poma/logs/poma-reconcile-cron.log`) and the current `/opt/poma/state/rebalance_state.json` directly from the GitHub Actions job log, without SSH.

Open the Gateway GUI only through a local tunnel over IAP SSH:

```bash
gcloud compute ssh poma-<env>-free-tier \
  --zone us-west1-b \
  --tunnel-through-iap \
  -- -L 5900:127.0.0.1:5900
```

Then connect a local VNC client to `127.0.0.1:5900`.

## Live trading gate

Do not switch to `trading_mode=live` until:

- Paper mode has run successfully for at least one full trading week.
- `allow_live_trading=true` is set intentionally.
- Order size, turnover, daily trade count, and position caps are reviewed.
- The latest rebalance report is manually reviewed.

IBKR authentication can still require operator action for mobile approval, session reset, or account prompts. The repo supervises and restarts Gateway, but it does not bypass broker authentication requirements.
