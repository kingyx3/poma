# IB Gateway operations

POMA runs IB Gateway on the same GCP e2-micro VM as the bot. The goal is one cheap host, supervised Gateway, and broker login credentials supplied from GitHub Environment Secrets only during explicit Gateway configure actions.

See [`adr/0001-ibkr-credentials-in-github-secrets.md`](adr/0001-ibkr-credentials-in-github-secrets.md) for the credential-storage decision.

## Production flow

Use this flow for manual paper/live setup and for production promotion. Auto CI/CD also invokes Gateway Ops automatically for dev pull requests and staging pushes when deploy or Gateway paths changed.

1. Deploy the VM using [`deployment-gcp-free-tier.md`](deployment-gcp-free-tier.md).
2. Add the required GitHub Environment Secrets for the target environment:

```text
IBKR_LOGIN_ID=<ibkr-gateway-login-username>
IBKR_LOGIN_SECRET=<ibkr-gateway-login-password>
```

3. Run **IB Gateway Ops** with `action=configure-paper` before paper mode, or `action=configure-live` before live mode.
4. Approve broker mobile authentication when prompted.
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

The **IB Gateway Ops** workflow reads `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET` from GitHub Environment Secrets only for `configure-paper` and `configure-live`, sends them to `sudo poma-configure-ibc` over IAP SSH stdin, and removes its temporary runner-side input file after use.

The same ops workflow repairs the Gateway runtime before `restart`, `verify-socket`, `configure-paper`, and `configure-live`. The repair is intentionally self-healing: it can reinstall missing headless packages, rebuild the runtime wrapper/service, install missing IB Gateway and IBC artifacts, fix stale `/tmp/poma-ibgateway` ownership, and move sidecar logs to the systemd-managed `/var/log/poma/ibgateway` directory. Pull-request Auto CI/CD uses `configure-paper` for the dev Gateway check so broker-login and authenticated API regressions are caught before merge when an operator approves IBKR mobile 2FA. Configure and socket verification wait for two stable `127.0.0.1:7497` polls before running the real `poma ibkr-check` handshake, print the redacted handshake tail on failure, and tolerate a transient post-socket service restart until the bounded readiness deadline.

The service starts raw IB Gateway until `/home/poma/ibc/config.ini` exists. After setup, it starts Gateway through IBC as one foreground systemd process and refuses to fall back to raw Gateway if the configured IBC launch path is broken.

## Credential handling rules

- Store IBKR Gateway login credentials only as GitHub Environment Secrets named `IBKR_LOGIN_ID` and `IBKR_LOGIN_SECRET`.
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
