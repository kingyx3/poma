# Gateway helper refresh

Use the **Refresh Gateway Helper** workflow when an existing VM has an old or incomplete Gateway helper install.

This is useful when **IB Gateway Ops** fails before the broker login prompt due to an old helper, a missing IBC template, or a missing Gateway systemd service.

Run:

1. **Actions** -> **Refresh Gateway Helper**.
2. Select the same `deploy_environment`, for example `dev`.
3. After it completes, rerun **IB Gateway Ops** with `configure-paper` or `configure-live`.

The refresh workflow copies the current helper installer and service repair script to the VM over IAP SSH. It reinstalls `/usr/local/bin/poma-configure-ibc` and recreates the Gateway service when the service unit is missing.
