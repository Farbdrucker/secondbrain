"""Short Telegram messages from the cron jobs, sent straight to the Bot API. Silent by default (night runs)."""

import json
import os
import sys
import urllib.request


def send(text: str, silent: bool = True) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id or os.environ.get("NOTIFY", "1") == "0":
        return
    body = json.dumps({"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True,
                       "disable_notification": silent}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # a lost notification must not fail the run
        print(f"notify failed: {type(e).__name__}", file=sys.stderr)
