#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import requests


def _extract_chat(update: dict[str, Any]) -> dict[str, Any] | None:
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "business_message",
    ):
        payload = update.get(key)
        if isinstance(payload, dict) and isinstance(payload.get("chat"), dict):
            return payload["chat"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print Telegram chat IDs from recent bot updates. Send a message to the bot first, "
            "or add it to the target group/channel, then run this helper."
        )
    )
    parser.add_argument(
        "--bot-token-env",
        default="TELEGRAM_BOT_TOKEN",
        help="Environment variable containing the bot token.",
    )
    parser.add_argument("--limit", default=20, type=int, help="Maximum updates to inspect.")
    args = parser.parse_args()

    token = os.environ.get(args.bot_token_env, "").strip()
    if not token:
        raise SystemExit(f"Missing {args.bot_token_env}")

    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": args.limit, "allowed_updates": '["message","channel_post"]'},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise SystemExit(f"Telegram getUpdates failed: {payload}")

    seen: set[int | str] = set()
    for update in payload.get("result", []):
        chat = _extract_chat(update)
        if not chat:
            continue
        chat_id = chat.get("id")
        if chat_id in seen:
            continue
        seen.add(chat_id)
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "unknown"
        print(f"{chat_id}\t{chat.get('type', 'unknown')}\t{title}")

    if not seen:
        print(
            "No chat IDs found. Message the bot first, or add it to the target group/channel, "
            "then run this again. If a webhook is configured, temporarily remove it before "
            "using getUpdates.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
