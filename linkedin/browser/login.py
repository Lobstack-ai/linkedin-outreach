# linkedin/browser/login.py
import logging

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

from linkedin.browser.nav import goto_page, human_type
from linkedin.conf import (
    BROWSER_DEFAULT_TIMEOUT_MS,
    BROWSER_HEADLESS,
    BROWSER_LOGIN_TIMEOUT_MS,
    BROWSER_SLOW_MO,
)

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

SELECTORS = {
    "email": 'input#username',
    "password": 'input#password',
    "submit": 'button[type="submit"]',
}


def playwright_login(session: "AccountSession"):
    page = session.page
    lp = session.linkedin_profile
    logger.info(colored("Fresh login sequence starting", "cyan") + f" for {session}")

    goto_page(
        session,
        action=lambda: page.goto(LINKEDIN_LOGIN_URL),
        expected_url_pattern="/login",
        error_message="Failed to load login page",
    )

    human_type(page.locator(SELECTORS["email"]), lp.linkedin_username)
    session.wait()
    human_type(page.locator(SELECTORS["password"]), lp.linkedin_password)
    session.wait()

    page.locator(SELECTORS["submit"]).click()
    page.wait_for_load_state("load")
    session.wait()

    # Handle LinkedIn security checkpoint
    current = page.url
    if "/checkpoint" in current or "/challenge" in current:
        logger.info(colored("LinkedIn security checkpoint detected!", "yellow", attrs=["bold"]))
        page.screenshot(path="/tmp/checkpoint.png")
        logger.info("Screenshot saved to /tmp/checkpoint.png")

        # Try multiple selectors for the verification code input
        code_input = None
        for selector in [
            'input#input__email_verification_pin',
            'input[name="pin"]',
            'input[name="verificationCode"]',
            'input[type="text"]',
            'input[type="number"]',
        ]:
            loc = page.locator(selector)
            if loc.count() > 0:
                code_input = loc.first
                break

        if code_input is not None:
            logger.info(colored("LinkedIn sent a verification code to your email.", "yellow"))
            code = input(">>> Enter the verification code from your email: ").strip()
            code_input.fill(code)
            page.wait_for_timeout(1000)
            submit = page.locator('button[type="submit"], button#btn-submit')
            if submit.count() > 0:
                submit.first.click()
                page.wait_for_load_state("load")
                page.wait_for_timeout(3000)
        else:
            logger.info("No code input found — this may be a CAPTCHA or different challenge.")
            logger.info("Saving page HTML for debugging...")
            page.screenshot(path="/tmp/checkpoint_page.png")
            with open("/tmp/checkpoint_page.html", "w") as f:
                f.write(page.content())

        # Check if we made it to feed
        current = page.url
        if "/feed" in current:
            logger.info(colored("Checkpoint passed!", "green", attrs=["bold"]))
        else:
            page.screenshot(path="/tmp/checkpoint_failed.png")
            raise RuntimeError(
                "Checkpoint not resolved. Screenshots saved to /tmp/checkpoint*.png. "
                "Try logging into LinkedIn from your normal browser to approve this server's IP, "
                "then restart the daemon."
            )
    elif "/feed" not in current:
        raise RuntimeError(f"Login failed – unexpected URL: {current}")


def launch_browser(storage_state=None):
    logger.debug("Launching Playwright")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=BROWSER_HEADLESS, slow_mo=BROWSER_SLOW_MO)
    context = browser.new_context(storage_state=storage_state)
    context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    return page, context, browser, playwright


def _save_cookies(session):
    """Persist Playwright storage state (cookies) to the DB."""
    state = session.context.storage_state()
    session.linkedin_profile.cookie_data = state
    session.linkedin_profile.save(update_fields=["cookie_data"])


def start_browser_session(session: "AccountSession"):
    logger.debug("Configuring browser for %s", session)

    session.linkedin_profile.refresh_from_db(fields=["cookie_data"])
    cookie_data = session.linkedin_profile.cookie_data

    storage_state = cookie_data if cookie_data else None
    if storage_state:
        logger.info("Loading saved session for %s", session)

    session.page, session.context, session.browser, session.playwright = launch_browser(storage_state=storage_state)

    if not storage_state:
        playwright_login(session)
        _save_cookies(session)
        logger.info(colored("Login successful – session saved", "green", attrs=["bold"]))
    else:
        goto_page(
            session,
            action=lambda: session.page.goto(LINKEDIN_FEED_URL),
            expected_url_pattern="/feed",
            timeout=BROWSER_DEFAULT_TIMEOUT_MS,
            error_message="Saved session invalid",
        )

    session.page.wait_for_load_state("load")
    logger.info(colored("Browser ready", "green", attrs=["bold"]))


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Start a LinkedIn browser session")
    args = parser.parse_args()
    session = cli_session(args)
    session.ensure_browser()

    start_browser_session(session=session)
    print("Logged in! Close browser manually.")
    session.page.pause()
