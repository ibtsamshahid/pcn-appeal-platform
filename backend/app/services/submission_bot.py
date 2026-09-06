"""
Sandbox submission bot.

Automates filling and submitting an APPROVED appeal onto a mock
PCN-provider website — the "AI bot posts the appeal" piece of the original
concept, deliberately scoped to a site we control rather than a real
council/operator portal (see README for the reasoning: Terms of Service
risk, anti-bot protection on real sites, and it isn't necessary to prove
the concept).

Design notes for the report:
- Only ever runs on appeals with approved == "approved" (enforced in the
  router, not here) — the human-in-the-loop gate is never bypassed.
- Headed vs headless is controlled by BOT_HEADLESS so the same code runs
  visibly for a live demo and invisibly/faster for routine testing.
- In headed mode the browser window is deliberately kept open for a few
  seconds AFTER the confirmation page loads (see BOT_DEMO_HOLD_SECONDS
  below), instead of closing the instant the form posts. Earlier versions
  closed immediately, which for a live demo meant the "submitted
  successfully" confirmation flashed and vanished before anyone could
  actually read it.
"""
import os
import threading

from playwright.sync_api import sync_playwright

MOCK_SITE_URL = os.getenv("MOCK_SITE_URL", "http://localhost:9000").rstrip("/")
BOT_HEADLESS = os.getenv("BOT_HEADLESS", "false").lower() == "true"

# How long (seconds) to leave the browser window open on the confirmation
# page after a successful headed submission, so a person watching the demo
# actually gets to see "Appeal Received" / the reference number rather than
# the window closing the instant the form posts. Only applies when
# BOT_HEADLESS is false — a headless run is invisible anyway, so there's
# nothing to show and it always closes immediately to free resources.
# Set BOT_DEMO_HOLD_SECONDS=0 to restore the old instant-close behaviour,
# or raise it (e.g. to 300) to comfortably hold it open for a longer demo
# — the window can always be closed manually before the hold ends too.
BOT_DEMO_HOLD_SECONDS = float(os.getenv("BOT_DEMO_HOLD_SECONDS", "60"))

# How long (seconds) the API request will wait for the bot to reach the
# confirmation page before giving up and reporting a timeout. This is
# separate from BOT_DEMO_HOLD_SECONDS above — the API response goes back
# as soon as the confirmation reference is scraped; the hold only delays
# when the (now-detached) browser window itself closes.
_SUBMISSION_TIMEOUT_SECONDS = 45

# Used by POST /appeals/submit-batch as the demo_hold_seconds override for
# every submission in the batch — short enough that submitting several
# PCNs' appeals in one go stays a reasonable wait (a few seconds per
# notice, not BOT_DEMO_HOLD_SECONDS' full ~60s each), while still holding
# the confirmation page open just long enough to be visible mid-demo if
# it's running headed.
BOT_BATCH_DEMO_HOLD_SECONDS = float(os.getenv("BOT_BATCH_DEMO_HOLD_SECONDS", "3"))


class BotSubmissionError(Exception):
    """Raised when the bot can't reach the sandbox site or the submission
    doesn't go through as expected (e.g. site down, form layout changed)."""


def submit_appeal_via_bot(notice, appeal, demo_hold_seconds: float = None) -> dict:
    """Launches a browser, navigates to the sandbox appeal form, fills it
    in from the notice/appeal data, submits, and scrapes the confirmation
    reference back off the resulting page.

    The actual browser automation runs in a background thread so the
    browser's lifetime isn't tied to this function returning: this
    function returns as soon as the confirmation is scraped (so the API
    response isn't delayed), while — in headed mode — the browser window
    itself is kept open separately for BOT_DEMO_HOLD_SECONDS so a demo
    audience can actually see the confirmation page. (Simply "not calling
    browser.close()" doesn't achieve this on its own: exiting Playwright's
    `sync_playwright()` context tears down the browser process regardless
    of whether close() was called explicitly.)

    `demo_hold_seconds`, if given, overrides BOT_DEMO_HOLD_SECONDS for
    this one call — used by the batch-submission endpoint
    (POST /appeals/submit-batch) to cut each browser's hold time way down,
    since holding the default ~60s open per notice would make submitting
    several PCNs at once take several minutes end to end. A single-notice
    submission keeps the full, demo-friendly default.

    Returns {"reference": str, "confirmation_text": str}.
    """
    hold_seconds = BOT_DEMO_HOLD_SECONDS if demo_hold_seconds is None else demo_hold_seconds
    letter_text = appeal.final_text or appeal.draft_text
    pcn_reference = str(notice.id)
    vehicle_registration = notice.vehicle_registration or "UNKNOWN"

    result: dict = {}
    error: dict = {}
    confirmed = threading.Event()

    def _run():
        try:
            with sync_playwright() as p:
                       browser = p.chromium.launch(
                    headless=BOT_HEADLESS,
                    slow_mo=150 if not BOT_HEADLESS else 0,  # slow down so a human watching can follow along
                )
                try:
                    page = browser.new_page()
                    page.goto(f"{MOCK_SITE_URL}/appeal-form", timeout=30000)
                    page.fill("#pcn_reference", pcn_reference)
                    page.fill("#vehicle_registration", vehicle_registration)
                    page.fill("#appellant_name", "Demo Driver")
                    page.fill("#appeal_text", letter_text)
                    page.click("#submit-btn")

                    page.wait_for_selector("#confirmation-reference", timeout=20000)
                    result["reference"] = page.inner_text("#confirmation-reference").strip()
                    result["confirmation_text"] = page.inner_text("#confirmation-message").strip()

                    # Data is captured — let the waiting API request return
                    # now. The browser stays open past this point purely for
                    # the demo; nothing below is on the API's critical path.
                    confirmed.set()

                    if not BOT_HEADLESS and hold_seconds > 0:
                        page.wait_for_timeout(hold_seconds * 1000)
                finally:
                    browser.close()
        except Exception as exc:
            error["exc"] = exc
            confirmed.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    confirmed.wait(timeout=_SUBMISSION_TIMEOUT_SECONDS)

    if "exc" in error:
        raise BotSubmissionError(
            f"Bot submission failed — is the sandbox site running at {MOCK_SITE_URL}? "
            f"Underlying error: {error['exc']}"
        ) from error["exc"]

    if not confirmed.is_set():
        raise BotSubmissionError(
            f"Bot submission timed out talking to the sandbox site at {MOCK_SITE_URL}."
        )

    return result
