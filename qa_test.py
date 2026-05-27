#!/usr/bin/env python3
"""
Humonex daily QA smoke test — full functional version.
Covers: login, Tara chat flows, Compilance manual flow, and all key pages.
Sends plain-English Discord alerts with AI screenshot analysis on any failure.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from ai_analyzer import analyze_screenshot
from screenshot_annotator import annotate_failure

load_dotenv()

APP_URL             = os.getenv("APP_URL", "https://app.humonex.com").rstrip("/")
TEST_EMAIL          = os.getenv("TEST_EMAIL")
TEST_PASSWORD       = os.getenv("TEST_PASSWORD")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
LOG_DIR             = Path(os.getenv("LOG_DIR", "./logs"))

LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"qa_{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("humonex-qa")

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def get_test_period():
    """Return (fy, month) for the *previous* calendar month.
    GSTR2B for month M is available/tested in month M+1, so we always go one month back."""
    now = datetime.now()
    if now.month == 1:
        m, y = 12, now.year - 1
    else:
        m, y = now.month - 1, now.year
    month = MONTHS[m - 1]
    fy_start = y if m >= 4 else y - 1
    fy = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    return fy, month


# Plain-English name for each step (shown in Discord)
STEP_NAME = {
    "login":               "Logging in to the app",
    "dashboard":           "Loading the Dashboard",
    "chat_open":           "Opening Tara Chat (Filling page)",
    "chat_check_status":   "Asking Tara: Check task status",
    "chat_dashboard_sum":  "Asking Tara: Dashboard summary",
    "chat_gstr2b":         "Starting GSTR2B download via Tara Chat",
    "chat_activity":       "Confirming Chat-triggered task appeared in Activity",
    "compilance_open":     "Opening Compilance page",
    "compilance_setup":    "Selecting client and setting download parameters",
    "compilance_start":    "Starting manual GSTR2B download from Compilance",
    "compilance_activity": "Confirming Compilance-triggered task appeared in Activity",
    "tara_overview":       "Checking Tara Overview page",
    "tasks_page":          "Checking Tasks page",
    "clients_page":        "Checking Clients page",
    "bulk_recon":          "Checking Bulk Reconciliation page",
}

# What to check manually when a step fails (shown in Discord)
STEP_MANUAL_CHECK = {
    "login":
        f"Open {APP_URL}/login and try logging in with the test account.",
    "dashboard":
        f"After login, check if {APP_URL}/dashboard shows data and stats.",
    "chat_open":
        f"Open {APP_URL}/chat and check if the 'Message Tara...' input box appears.",
    "chat_check_status":
        "On the Chat page, click 'Check task status'. Does Tara reply?",
    "chat_dashboard_sum":
        "On the Chat page, click 'Dashboard summary'. Does Tara reply with a summary?",
    "chat_gstr2b":
        "On the Chat page, click 'Download GSTR2B for all clients'. "
        "A popup should appear asking for Financial Year and Month. "
        "Select the current period and click 'Start Download'.",
    "chat_activity":
        f"Go to {APP_URL}/activity. Can you see a GSTR2B task triggered from Chat? "
        "If not, the task may not have been created by the app.",
    "compilance_open":
        f"Open {APP_URL}/compilance. Does the client list load on the left?",
    "compilance_setup":
        "On the Compilance page, tick the checkbox next to 'Itechnotion', "
        "set Task Type to GSTR2B, choose the current financial year and month, "
        "and make sure 'Send Email After Download' is NOT ticked.",
    "compilance_start":
        "After selecting a client and setting params, click 'Start Download'. "
        "Does the button respond and the download begin?",
    "compilance_activity":
        f"Go to {APP_URL}/activity. Can you see a GSTR2B task from Compilance? "
        "If not, the app may not have tracked the manual download.",
    "tara_overview":
        f"Open {APP_URL}/tara and check if the Tara Overview renders correctly.",
    "tasks_page":
        f"Open {APP_URL}/tasks and check if the task list loads.",
    "clients_page":
        f"Open {APP_URL}/clients and check if the client list loads.",
    "bulk_recon":
        f"Open {APP_URL}/bulk-reconciliation and check if the page loads without errors.",
}


def _plain_error(raw: str) -> str:
    e = raw.lower()
    if "timeout" in e:
        return (
            "The expected button or element did not appear within the time limit. "
            "The app may be slow, or the page layout may have changed."
        )
    if "net::err" in e or "connection refused" in e:
        return "Could not reach the app. The server may be down or unreachable."
    if "not found" in e or "unable to find" in e:
        return "The expected element could not be found on the page."
    if "navigation" in e:
        return "The page did not navigate to the expected URL."
    return raw[:300]


# ─────────────────────────────────────────────────────────────────────────────
class QARunner:
    def __init__(self, page):
        self.page    = page
        self.results = []
        self.start   = datetime.now()
        self._triggered_chat           = None
        self._triggered_compilance     = None
        self._compilance_already_done  = False

    def _ss(self, full=False):
        try:
            return self.page.screenshot(full_page=full)
        except Exception:
            return None

    def _pass(self, key):
        self.results.append({"key": key, "name": STEP_NAME[key], "status": "pass"})
        logger.info(f"✅  {STEP_NAME[key]}")
        return True

    def _select_verified(self, select_locator, wanted_text):
        """Select an option by text, scanning actual options if a direct match fails. Raises on failure."""
        try:
            select_locator.select_option(wanted_text)
        except Exception:
            try:
                select_locator.select_option(label=wanted_text)
            except Exception:
                pass

        selected = select_locator.evaluate(
            "el => el.options[el.selectedIndex]?.text?.trim() || ''"
        )
        if wanted_text.lower() in selected.lower():
            return

        opts = select_locator.evaluate(
            "el => [...el.options].map(o => ({v: o.value, t: o.text.trim()}))"
        )
        logger.warning(
            f"Select: wanted '{wanted_text}', got '{selected}'. "
            f"Available options: {[o['t'] for o in opts]}"
        )
        match = next((o for o in opts if wanted_text.lower() in o["t"].lower()), None)
        if match:
            select_locator.select_option(value=match["v"])
            return
        raise Exception(
            f"Option '{wanted_text}' not found in dropdown. "
            f"Available: {[o['t'] for o in opts]}"
        )

    def _fail(self, key, raw_error, screenshot=None, ai_note=None):
        ss = screenshot or self._ss()
        if ss and not ai_note:
            ai_note = analyze_screenshot(
                ss,
                f"Humonex QA bot: the step '{STEP_NAME[key]}' just failed.",
                "Describe in plain English what you see on this screen. "
                "Is there an error message, a blank area, or something unexpected? "
                "2 sentences max.",
            )
        if ss:
            ss = annotate_failure(ss, STEP_NAME[key], _plain_error(str(raw_error)))
        self.results.append({
            "key":          key,
            "name":         STEP_NAME[key],
            "status":       "fail",
            "error_raw":    raw_error,
            "error_plain":  _plain_error(str(raw_error)),
            "manual_check": STEP_MANUAL_CHECK[key],
            "screenshot":   ss,
            "ai_note":      ai_note,
        })
        logger.error(f"❌  {STEP_NAME[key]}: {raw_error}")
        if ai_note:
            logger.error(f"    AI: {ai_note}")
        return False

    def _ai(self, context, question):
        ss   = self._ss()
        note = analyze_screenshot(ss, context, question) if ss else "(no screenshot)"
        logger.info(f"AI → {note}")
        return ss, note

    def _poll_activity(self, task_label, triggered_at, max_sec=120, interval=15):
        """
        Poll /activity every interval seconds until task_label appears with a
        final status (COMPLETED or FAILED). Both count as the app working correctly —
        only a timeout means the app failed to track the task.
        """
        logger.info(
            f"Polling /activity for '{task_label}' (max {max_sec}s, every {interval}s) …"
        )
        deadline = time.time() + max_sec

        while time.time() < deadline:
            try:
                self.page.goto(
                    f"{APP_URL}/activity", wait_until="networkidle", timeout=30000
                )
                self.page.wait_for_timeout(2000)
            except Exception:
                pass

            body = self.page.locator("body").inner_text()
            ss   = self._ss(full=True)

            if task_label.lower() in body.lower():
                ai = analyze_screenshot(
                    ss,
                    f"Activity page of Humonex. A '{task_label}' task was triggered at "
                    f"{triggered_at.strftime('%H:%M:%S')}. "
                    f"There may be multiple {task_label} entries; focus on the most recent.",
                    f"What is the status of the most recent '{task_label}' task? "
                    f"Reply with exactly one word on the FIRST line: "
                    f"COMPLETED, IN_PROGRESS, or FAILED. "
                    f"Then on the next line, one sentence describing what you see.",
                )
                first = (ai or "").strip().split("\n")[0].upper()
                logger.info(f"Poll → {first}")

                if "COMPLETED" in first or "FAILED" in first:
                    return True, ss, ai
                # IN_PROGRESS → keep waiting

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self.page.wait_for_timeout(int(min(interval, remaining) * 1000))

        try:
            self.page.goto(
                f"{APP_URL}/activity", wait_until="networkidle", timeout=30000
            )
        except Exception:
            pass
        return (
            False,
            self._ss(full=True),
            f"The '{task_label}' task did not appear in Activity within "
            f"{max_sec} seconds after being triggered.",
        )

    # ── Steps ─────────────────────────────────────────────────────────────────

    def step_login(self):
        try:
            self.page.goto(
                f"{APP_URL}/login", wait_until="domcontentloaded", timeout=30000
            )
            self.page.locator(
                'input[type="email"], input[name="email"], input[id*="email" i]'
            ).first.fill(TEST_EMAIL)
            self.page.locator(
                'input[type="password"], input[name="password"]'
            ).first.fill(TEST_PASSWORD)
            self.page.locator(
                'button[type="submit"], button:has-text("Login"), button:has-text("Sign in")'
            ).first.click()
            self.page.wait_for_function(
                "() => !window.location.pathname.includes('/login')", timeout=20000
            )
            return self._pass("login")
        except Exception as e:
            return self._fail("login", str(e))

    def step_dashboard(self):
        try:
            self.page.goto(
                f"{APP_URL}/dashboard", wait_until="networkidle", timeout=30000
            )
            if len(self.page.locator("body").inner_text()) < 100:
                raise Exception("Dashboard page appears empty")
            self._ai(
                "Humonex dashboard page after login.",
                "Does this dashboard show real content (stats, data)? "
                "Or is it blank/showing an error? One sentence.",
            )
            return self._pass("dashboard")
        except Exception as e:
            return self._fail("dashboard", str(e))

    def step_chat_open(self):
        try:
            self.page.goto(
                f"{APP_URL}/chat", wait_until="networkidle", timeout=30000
            )
            self.page.locator(
                'input[placeholder="Message Tara..."]'
            ).wait_for(state="visible", timeout=10000)
            return self._pass("chat_open")
        except Exception as e:
            return self._fail("chat_open", str(e))

    def _reload_chat_and_click(self, button_text):
        self.page.goto(f"{APP_URL}/chat", wait_until="networkidle", timeout=30000)
        self.page.locator(
            'input[placeholder="Message Tara..."]'
        ).wait_for(state="visible", timeout=10000)
        btn = self.page.locator(f'button:has-text("{button_text}")')
        btn.wait_for(state="visible", timeout=10000)
        btn.click()

    def step_chat_check_status(self):
        try:
            self._reload_chat_and_click("Check task status")
            self.page.wait_for_timeout(5000)
            self._ai(
                "Tara Chat after clicking 'Check task status'.",
                "Did Tara respond with task status info? Is there a new message? One sentence.",
            )
            return self._pass("chat_check_status")
        except Exception as e:
            return self._fail("chat_check_status", str(e))

    def step_chat_dashboard_sum(self):
        try:
            self._reload_chat_and_click("Dashboard summary")
            self.page.wait_for_timeout(5000)
            self._ai(
                "Tara Chat after clicking 'Dashboard summary'.",
                "Did Tara respond with a summary? Is there a new message? One sentence.",
            )
            return self._pass("chat_dashboard_sum")
        except Exception as e:
            return self._fail("chat_dashboard_sum", str(e))

    def step_chat_gstr2b(self):
        fy, month = get_test_period()
        try:
            self._reload_chat_and_click("Download GSTR2B for all clients")

            # Wait for the period-selection modal (a <select> appears on the page)
            self.page.locator("select").first.wait_for(state="visible", timeout=10000)
            selects = self.page.locator("select").all()

            # Financial Year (first dropdown in modal)
            if selects:
                self._select_verified(selects[0], fy)

            # Month (second dropdown in modal)
            if len(selects) >= 2:
                self._select_verified(selects[1], month)

            self.page.wait_for_timeout(500)

            # "Start Download" inside the modal — only button with this text on /chat
            start = self.page.locator('button:has-text("Start Download")').first
            start.wait_for(state="visible", timeout=10000)
            start.click()

            self._triggered_chat = datetime.now()
            self.page.wait_for_timeout(3000)

            self._ai(
                f"Tara Chat after triggering GSTR2B download for {month} {fy}.",
                "Did Tara acknowledge the download or confirm the task started? One sentence.",
            )
            return self._pass("chat_gstr2b")
        except Exception as e:
            return self._fail("chat_gstr2b", str(e))

    def step_chat_activity(self):
        try:
            triggered = self._triggered_chat or datetime.now()
            found, ss, ai = self._poll_activity("GSTR2B", triggered)
            if not found:
                return self._fail("chat_activity", ai, ss, ai)
            logger.info(f"Chat activity: {ai}")
            return self._pass("chat_activity")
        except Exception as e:
            return self._fail("chat_activity", str(e))

    def step_compilance_open(self):
        try:
            self.page.goto(
                f"{APP_URL}/compilance", wait_until="networkidle", timeout=30000
            )
            self.page.wait_for_timeout(2000)
            if len(self.page.locator("body").inner_text()) < 50:
                raise Exception("Compilance page appears empty")
            return self._pass("compilance_open")
        except Exception as e:
            return self._fail("compilance_open", str(e))

    def _try_select(self, label_texts, value, fallback_text=None):
        """Try to set a <select> by label. Falls back to scanning all selects."""
        for label in label_texts:
            try:
                sel = self.page.get_by_label(label)
                try:
                    sel.select_option(value)
                except Exception:
                    sel.select_option(label=value)
                return
            except Exception:
                pass
        # Fallback: scan all selects for one that has this option
        if fallback_text:
            for sel in self.page.locator("select").all():
                try:
                    opts = sel.evaluate("el => [...el.options].map(o => o.text)")
                    if fallback_text in opts:
                        sel.select_option(label=fallback_text)
                        return
                except Exception:
                    pass

    def step_compilance_setup(self):
        fy, month = get_test_period()
        try:
            # Checkbox layout on /compilance:
            #   nth(0) = header "select all" row
            #   nth(1) = first client row
            #   last   = "Send Email After Download"
            all_cbs = self.page.locator('input[type="checkbox"]')
            all_cbs.nth(0).wait_for(state="visible", timeout=10000)

            client_cb = all_cbs.nth(1)
            if not client_cb.is_checked():
                client_cb.click()
            self.page.wait_for_timeout(500)

            # Select layout on /compilance (right panel):
            #   nth(0) = Task Type
            #   nth(1) = Financial Year
            #   nth(2) = Period (Month)
            selects = self.page.locator("select")

            self._select_verified(selects.nth(0), "GSTR2B")
            self._select_verified(selects.nth(1), fy)
            self._select_verified(selects.nth(2), month)

            # Ensure "Send Email After Download" is unchecked — real emails on file
            send_email_cb = all_cbs.last
            if send_email_cb.is_checked():
                send_email_cb.uncheck()

            return self._pass("compilance_setup")
        except Exception as e:
            return self._fail("compilance_setup", str(e))

    def step_compilance_start(self):
        try:
            start = self.page.locator('button:has-text("Start Download")').first
            start.wait_for(state="visible", timeout=10000)
            start.click()
            self._triggered_compilance = datetime.now()
            self.page.wait_for_timeout(3000)

            body = self.page.locator("body").inner_text()
            if "already downloaded" in body.lower():
                # GSTR2B for this period was already fetched — app correctly blocked the duplicate.
                # No new /activity entry will be created, so mark compilance_activity skip flag.
                self._compilance_already_done = True
                logger.info(
                    "Compilance: 'Already Downloaded' — GSTR2B already exists for "
                    "this period; no new task will be queued (treating as PASS)"
                )
                self._ai(
                    "Compilance page showing an 'Already Downloaded' notification.",
                    "What period/month does the notification mention? One sentence.",
                )
                return self._pass("compilance_start")

            self._ai(
                "Compilance page after clicking Start Download for GSTR2B.",
                "Is there a progress indicator or confirmation that the download started? "
                "One sentence.",
            )
            return self._pass("compilance_start")
        except Exception as e:
            return self._fail("compilance_start", str(e))

    def step_compilance_activity(self):
        try:
            if self._compilance_already_done:
                logger.info(
                    "Compilance activity: skipped — GSTR2B was already downloaded, "
                    "no new task expected in /activity (PASS)"
                )
                return self._pass("compilance_activity")
            triggered = self._triggered_compilance or datetime.now()
            found, ss, ai = self._poll_activity("GSTR2B", triggered)
            if not found:
                return self._fail("compilance_activity", ai, ss, ai)
            logger.info(f"Compilance activity: {ai}")
            return self._pass("compilance_activity")
        except Exception as e:
            return self._fail("compilance_activity", str(e))

    def step_page_check(self, key, path, ai_context):
        try:
            self.page.goto(
                f"{APP_URL}{path}", wait_until="networkidle", timeout=30000
            )
            self.page.wait_for_timeout(1500)
            if len(self.page.locator("body").inner_text()) < 50:
                raise Exception(f"{path} appears empty or did not load")
            self._ai(
                ai_context,
                "Does this page look normal and functional? "
                "Or is it blank, broken, or showing an error? One sentence.",
            )
            return self._pass(key)
        except Exception as e:
            return self._fail(key, str(e))

    # ── Orchestrator ──────────────────────────────────────────────────────────

    def run(self):
        steps = [
            self.step_login,
            self.step_dashboard,
            self.step_chat_open,
            self.step_chat_check_status,
            self.step_chat_dashboard_sum,
            self.step_chat_gstr2b,
            self.step_chat_activity,
            self.step_compilance_open,
            self.step_compilance_setup,
            self.step_compilance_start,
            self.step_compilance_activity,
            lambda: self.step_page_check("tara_overview", "/tara",
                                         "Tara Overview page of Humonex."),
            lambda: self.step_page_check("tasks_page", "/tasks",
                                         "Tasks page of Humonex."),
            lambda: self.step_page_check("clients_page", "/clients",
                                         "Clients list page of Humonex."),
            lambda: self.step_page_check("bulk_recon", "/bulk-reconciliation",
                                         "Bulk Reconciliation page of Humonex."),
        ]

        for fn in steps:
            fn()  # run every step — never stop early

        duration     = (datetime.now() - self.start).total_seconds()
        passed       = sum(1 for r in self.results if r["status"] == "pass")
        failed_steps = [r for r in self.results if r["status"] == "fail"]

        return {
            "success":      len(failed_steps) == 0,
            "passed":       passed,
            "total":        len(STEP_NAME),
            "failed_steps": failed_steps,
            "duration_s":   duration,
            "timestamp":    self.start.isoformat(),
            "all_results":  self.results,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Discord formatting
# ─────────────────────────────────────────────────────────────────────────────

def _dur(s):
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec}s" if m else f"{sec}s"


def format_pass_message(result):
    lines = "\n".join(f"  ✅  {r['name']}" for r in result["all_results"])
    return (
        f"✅  **Daily QA Test Passed — Humonex**\n"
        f"All {result['passed']}/{result['total']} steps completed successfully.\n"
        f"Time: {result['timestamp'][:19].replace('T', ' ')} IST  |  "
        f"Duration: {_dur(result['duration_s'])}\n\n"
        f"**Steps completed:**\n{lines}"
    )


def format_summary_message(result):
    """One message showing all 15 steps with pass/fail icons — sent first."""
    ts    = result["timestamp"][:19].replace("T", " ")
    dur   = _dur(result["duration_s"])
    n_fail = len(result["failed_steps"])
    lines = []
    for r in result["all_results"]:
        icon = "✅" if r["status"] == "pass" else "❌"
        lines.append(f"  {icon}  {r['name']}")
    steps_str = "\n".join(lines)
    return (
        f"❌  **Daily QA Test — {n_fail} failure{'s' if n_fail != 1 else ''} — Humonex**\n"
        f"Time: {ts} IST  |  Duration: {dur}  |  "
        f"Steps passed: {result['passed']}/{result['total']}\n\n"
        f"**Full results (screenshots follow below):**\n{steps_str}"
    )[:1950]


def format_failure_detail(failure, index, total):
    """One message per failed step — sent after the summary, with screenshot attached."""
    ai  = (failure.get("ai_note") or "").strip()
    msg = (
        f"**Failure {index}/{total} — {failure['name']}**\n\n"
        f"**What went wrong:**\n{failure['error_plain']}\n\n"
        f"**What to check manually:**\n{failure['manual_check']}\n\n"
    )
    if ai:
        msg += f"**What the AI sees in the screenshot:**\n{ai}"
    return msg[:1950]


def send_discord(content, screenshot_bytes=None):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord alert")
        return
    payload = {"content": content, "username": "Humonex QA Bot"}
    try:
        if screenshot_bytes:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files={"file": ("failure.png", screenshot_bytes, "image/png")},
                timeout=20,
            )
        else:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Discord send failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not TEST_EMAIL or not TEST_PASSWORD:
        send_discord(
            "❌  **Humonex QA Bot — Config Error**\n"
            "TEST_EMAIL or TEST_PASSWORD is missing from .env"
        )
        sys.exit(2)

    logger.info(f"Starting Humonex full QA test → {APP_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1366, "height": 800})
        page    = context.new_page()

        runner = QARunner(page)
        result = runner.run()
        browser.close()

    logger.info(
        f"Test complete — success={result['success']}, "
        f"passed={result['passed']}/{result['total']}, "
        f"duration={_dur(result['duration_s'])}"
    )

    if result["success"]:
        send_discord(format_pass_message(result))
    else:
        send_discord(format_summary_message(result))
        for i, failure in enumerate(result["failed_steps"], 1):
            time.sleep(1)
            send_discord(
                format_failure_detail(failure, i, len(result["failed_steps"])),
                screenshot_bytes=failure.get("screenshot"),
            )

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()