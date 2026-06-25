# Operator setup

## Telegram chat ID

The Telegram bot token proves which bot is sending the alert. The chat ID selects the destination for the alert. Both are required by the current alert sender.

Steps:

1. Create the bot and copy its token.
2. Message the bot from the target account, or add the bot to the target group or channel and send a message there.
3. Run:

```bash
export TELEGRAM_BOT_TOKEN='your-bot-token'
python ops/scripts/get_telegram_chat_id.py
```

4. Copy the first column from the matching row into the `TELEGRAM_CHAT_ID` GitHub Environment secret.

If the bot already uses a webhook, remove the webhook before using this helper because Telegram long polling does not work while a webhook is active.

## Tailscale node registration

The simplest production path is to create a Tailscale auth key in the admin console and save it as the `TAILSCALE_AUTHKEY` GitHub Environment secret.

For less manual key rotation, Tailscale also supports OAuth clients with the auth-key scope. That lets automation create fresh node registration keys when needed. To use that model later, create a Tailscale OAuth client with an allowed device tag and store the OAuth client ID and secret in GitHub instead of a static node-registration key.
