# Gateway helper refresh

Use the **Refresh Gateway Helper** workflow when an existing VM has an old or incomplete Gateway helper install.

This is useful when **IB Gateway Ops** fails before the broker login prompt with a missing template file under `/opt/ibc`.

Run:

1. **Actions** -> **Refresh Gateway Helper**.
2. Select the same `deploy_environment`, for example `dev`.
3. After it completes, rerun **IB Gateway Ops** with `configure-paper` or `configure-live`.

The refresh workflow copies the current helper installer to the VM over IAP SSH and reinstalls `/usr/local/bin/poma-configure-ibc`.
