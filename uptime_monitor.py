#!/usr/bin/env python3
"""
Humonex uptime monitor.
Run every 5 minutes via cron. Sends a Discord alert ONLY when the website
changes state: UP→DOWN or DOWN→UP. No notification if nothing changed.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

APP_URL             = os.getenv("APP_URL", "https://app.humonex.com").rstrip("/")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
LOG_DIR             = Path(os.getenv("LOG_DIR", "./logs"))
STATE_FILE          = LOG_DIR / "uptime_state.json"


def is_up():
    """Return True if the site responds with a non-5xx status within 15 seconds."""
    try:
        r = requests.get(APP_URL, timeout=15, allow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"status": "unknown", "since": None}


def save_state(status, since):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"status": status, "since": since}))


def send_discord(content):
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL not set — skipping")
        return
    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": content, "username": "Humonex Uptime Monitor"},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"Discord send failed: {e}", file=sys.stderr)


def main():
    now            = datetime.now()
    now_str        = now.strftime("%Y-%m-%d %H:%M")
    current_status = "up" if is_up() else "down"

    state       = load_state()
    prev_status = state.get("status", "unknown")
    prev_since  = state.get("since")

    # First ever run — just record the baseline, no alert
    if prev_status == "unknown":
        save_state(current_status, now.isoformat())
        print(f"[{now_str}] First run — baseline recorded: {current_status}")
        return

    # No change — nothing to do
    if current_status == prev_status:
        print(f"[{now_str}] No change — site is {current_status}")
        return

    # ── State changed ────────────────────────────────────────────────────────
    if current_status == "down":
        msg = (
            f"🔴  **Website is DOWN — Humonex**\n"
            f"App went offline at **{now_str} IST**\n"
            f"URL: {APP_URL}\n"
            f"Check your server or hosting dashboard."
        )
    else:
        # Calculate how long it was down
        try:
            down_since  = datetime.fromisoformat(prev_since)
            minutes     = max(1, int((now - down_since).total_seconds() / 60))
            downtime    = f"{minutes} minute{'s' if minutes != 1 else ''}"
        except Exception:
            downtime = "unknown duration"

        msg = (
            f"🟢  **Website is back UP — Humonex**\n"
            f"App came back online at **{now_str} IST**\n"
            f"Was down for: **{downtime}**"
        )

    send_discord(msg)
    save_state(current_status, now.isoformat())
    print(f"[{now_str}] State changed {prev_status} → {current_status} — Discord notified")


if __name__ == "__main__":
    main()
