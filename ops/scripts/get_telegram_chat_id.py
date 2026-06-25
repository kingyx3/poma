#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

CHAT_PAYLOAD_KEYS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "my_chat_member",
    "chat_member",
)
ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "my_chat_member",
    "chat_member",
]


def _extract_chat(update: dict[str, Any]) -> dict[str, Any] | None:
    for key in CHAT_PAYLOAD_KEYS:
        payload = update.get(key)
        if isinstance(payload, dict) and isinstance(payload.get("chat"), dict):
            return payload["chat"]

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        message = callback_query.get("message")
        if isinstance(message, dict) and isinstance(message.get("chat"), dict):
            return message["chat"]

    return None


def _chat_title(chat: dict[str, Any]) -> str:
    title = chat.get("title") or chat.get("username") or chat.get("first_name") or "unknown"
    return str(title).replace("\t", " ")


def _telegram_request(
    session: requests.Session,
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    response = session.get(
        f"https://api.telegram.org/bot{token}/{method}",
        params=params,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise SystemExit(f"Telegram {method} HTTP {response.status_code}: {response.text}") from exc

    payload = response.json()
    if not payload.get("ok"):
        description = payload.get("description") or payload
        raise SystemExit(f"Telegram {method} failed: {description}")
    return payload


def _ensure_get_updates_available(
    session: requests.Session,
    token: str,
    delete_webhook: bool,
) -> None:
    payload = _telegram_request(session, token, "getWebhookInfo")
    webhook_url = payload.get("result", {}).get("url")
    if not webhook_url:
        return

    if not delete_webhook:
        raise SystemExit(
            "Telegram webhook is configured, so getUpdates cannot read chat IDs. Re-run with "
            "delete_webhook=true in GitHub Actions, or remove the webhook temporarily."
        )

    print(
        "Telegram webhook is configured; deleting it with drop_pending_updates=false before "
        "reading getUpdates.",
        file=sys.stderr,
    )
    _telegram_request(
        session,
        token,
        "deleteWebhook",
        params={"drop_pending_updates": "false"},
    )


def _read_chat_ids(
    session: requests.Session,
    token: str,
    limit: int,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> dict[int | str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    chats: dict[int | str, dict[str, Any]] = {}
    offset: int | None = None

    while time.monotonic() < deadline:
        remaining_seconds = max(1, int(deadline - time.monotonic()))
        telegram_timeout = min(10, remaining_seconds)
        params: dict[str, Any] = {
            "limit": limit,
            "timeout": telegram_timeout,
            "allowed_updates": json.dumps(ALLOWED_UPDATES),
        }
        if offset is not None:
            params["offset"] = offset

        payload = _telegram_request(
            session,
            token,
            "getUpdates",
            params=params,
            timeout=telegram_timeout + 10,
        )
        updates = payload.get("result", [])
        update_ids = [
            update["update_id"]
            for update in updates
            if isinstance(update, dict) and isinstance(update.get("update_id"), int)
        ]
        if update_ids:
            offset = max(update_ids) + 1

        for update in updates:
            if not isinstance(update, dict):
                continue
            chat = _extract_chat(update)
            if not chat:
                continue
            chat_id = chat.get("id")
            if chat_id is None or chat_id in chats:
                continue
            chats[chat_id] = chat

        if chats:
            return chats

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval_seconds, remaining))

    return chats


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print Telegram chat IDs from bot updates. Start this helper, then send a fresh "
            "message to the bot or target group/channel while it is polling."
        )
    )
    parser.add_argument(
        "--bot-token-env",
        default="TELEGRAM_BOT_TOKEN",
        help="Environment variable containing the bot token.",
    )
    parser.add_argument("--limit", default=100, type=int, help="Maximum updates per poll.")
    parser.add_argument(
        "--timeout-seconds",
        default=120,
        type=int,
        help="How long to wait for a fresh bot update before failing.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        default=3.0,
        type=float,
        help="Delay between Telegram polling attempts.",
    )
    parser.add_argument(
        "--delete-webhook",
        action="store_true",
        help="Delete an existing Telegram webhook with drop_pending_updates=false before polling.",
    )
    args = parser.parse_args()

    token = os.environ.get(args.bot_token_env, "").strip()
    if not token:
        raise SystemExit(f"Missing {args.bot_token_env}")

    session = requests.Session()
    _ensure_get_updates_available(session, token, delete_webhook=args.delete_webhook)

    chats = _read_chat_ids(
        session,
        token,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    for chat_id, chat in chats.items():
        print(f"{chat_id}\t{chat.get('type', 'unknown')}\t{_chat_title(chat)}")

    if not chats:
        print(
            f"No chat IDs found after waiting {args.timeout_seconds}s. Send a fresh /start or "
            "message after starting this workflow; messages sent before the run may already have "
            "been consumed by Telegram. For groups/channels, add the bot and send a new message "
            "there. If a webhook is configured, re-run with delete_webhook=true.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
