#!/usr/bin/env python3
"""
Railway entry point.
Runs as a single long-running process:
  - QA test        : daily at 9:30 AM IST (= 04:00 UTC, Railway runs in UTC)
  - Uptime monitor : every 5 minutes, state kept in memory
"""

import json
import logging
import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

import requests
import schedule
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")  # no-op on Railway; env vars come from dashboard

APP_URL             = os.getenv("APP_URL", "https://app.humonex.com").rstrip("/")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("humonex-scheduler")

LOG_DIR    = Path(__file__).parent / "logs"
STATE_FILE = LOG_DIR / "uptime_state.json"


def _discord(content):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": content, "username": "Humonex Uptime Monitor"},
            timeout=15,
        ).raise_for_status()
    except Exception as e:
        logger.error(f"Discord send failed: {e}")


def _load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        data["since"] = datetime.fromisoformat(data["since"])
        return data
    except Exception:
        return {}


def _save_state(status, since):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"status": status, "since": since.isoformat()}))


def check_uptime():
    now     = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    try:
        r       = requests.get(APP_URL, timeout=15, allow_redirects=True)
        current = "up" if r.status_code < 500 else "down"
    except Exception:
        current = "down"

    state = _load_state()

    if not state:
        # First ever run — record baseline, no alert
        _save_state(current, now)
        logger.info(f"Uptime baseline recorded: site is {current}")
        return

    prev = state["status"]
    if current == prev:
        logger.info(f"Uptime check: site is {current} (no change)")
        return

    # State changed — send Discord alert
    if current == "down":
        msg = (
            f"🔴  **Website is DOWN — Humonex**\n"
            f"Went offline at **{now_str} IST**\n"
            f"URL: {APP_URL}\n"
            f"Check your Railway dashboard or hosting logs."
        )
    else:
        minutes = max(1, int((now - state["since"]).total_seconds() / 60))
        msg = (
            f"🟢  **Website is back UP — Humonex**\n"
            f"Back online at **{now_str} IST**\n"
            f"Was down for: **{minutes} minute{'s' if minutes != 1 else ''}**"
        )

    _discord(msg)
    logger.info(f"Uptime changed: {prev} → {current}")
    _save_state(current, now)


def run_qa():
    logger.info("Starting scheduled daily QA test...")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "qa_test.py")],
            cwd=str(Path(__file__).parent),
            timeout=600,
        )
        logger.info(f"QA test finished — exit code {result.returncode}")
    except Exception as e:
        logger.error(f"QA test failed to launch: {e}")


# ── Schedule ──────────────────────────────────────────────────────────────────
schedule.every(5).minutes.do(check_uptime)
# 9:30 AM IST = 04:00 UTC
schedule.every().day.at("04:00").do(run_qa)

logger.info("Scheduler started — QA at 04:00 UTC (9:30 AM IST) | uptime every 5 min")

# Uptime check immediately on boot
check_uptime()

while True:
    schedule.run_pending()
    time.sleep(30)
