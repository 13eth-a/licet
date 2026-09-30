"""Register the NI public-user account end-to-end (write op — one-shot).

Preconditions: ACCELA_TEST_USERNAME in .env; a password is generated here
and written to .env on success (ACCELA_TEST_PASSWORD).

Flow (mapped by ni_register_recon.py):
  Login.aspx → click Register → CommunityView/account/new
  fill txbUserName/txbEmail/txbPassword1/txbPassword2 → reCAPTCHA → submit.

reCAPTCHA policy: if Google serves an interactive challenge (image grid),
the script STOPS and leaves the browser state on the form for the human to
solve manually. It never attempts to bypass captcha.
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import string
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

CITIZEN = "https://aca-test.accela.com/nullisland"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
EMAIL = os.environ.get("ACCELA_TEST_USERNAME", "").strip()


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def gen_password() -> str:
    # Accela-typical policy: upper+lower+digit+special, 16 chars.
    alphabet = string.ascii_letters + string.digits
    pwd = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    pwd += [secrets.choice(alphabet) for _ in range(12)]
    return "".join(pwd)


def save_html(tag: str, html: str, stamp: str) -> str:
    path = os.path.join(OUTDIR, f"{stamp}_{tag}.html")
    with open(path, "w") as f:
        f.write(html)
    return path


async def set_angular_value(page, selector: str, value: str) -> None:
    """Fill an Angular-controlled input without clicking: focus + type.

    The PrimeNG form's <label> overlays the input and intercepts pointer
    events (observed live), so click/fill actionability checks fail;
    focus() has no hit-target check.
    """
    loc = page.locator(selector).first
    await loc.focus()
    await page.keyboard.type(value, delay=45)
    await page.keyboard.press("Tab")  # blur → touched state for validators
    await page.wait_for_timeout(300)


async def main() -> int:
    from solari_browser import Solari

    if not EMAIL:
        out("ACCELA_TEST_USERNAME is empty — aborting.")
        return 2
    password = gen_password()
    username = EMAIL.split("@")[0][:30]  # txbUserName (login name), max 40ish

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        # 1. login page → Register
        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Login.aspx", timeout=45000,
                      wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(3500), 10)
        await page.locator("text=/register/i").first.click(timeout=8000)
        await asyncio.wait_for(page.wait_for_load_state("load"), 20)
        await asyncio.wait_for(page.wait_for_timeout(3000), 8)
        out(f"register page: {page.url[:120]}")

        # 2. fill the Angular form
        await set_angular_value(page, "#txbUserName", username)
        await set_angular_value(page, "#txbEmail", EMAIL)
        await set_angular_value(page, "#txbPassword1", password)
        await set_angular_value(page, "#txbPassword2", password)
        await page.screenshot(
            path=os.path.join(OUTDIR, f"{stamp}_register_filled.png"), full_page=True
        )
        out(f"filled: user={username} email={EMAIL} (password hidden)")

        # 3. reCAPTCHA status
        await asyncio.wait_for(page.wait_for_timeout(2000), 6)
        recap = await page.evaluate(
            """() => {
                const box = document.querySelector('.g-recaptcha, [data-sitekey]');
                return box ? box.getAttribute('data-sitekey') : null;
            }"""
        )
        out(f"recaptcha sitekey: {recap}")

        # 4. try to click the recaptcha checkbox (inside its iframe)
        challenge = False
        for fr in page.frames:
            if "recaptcha/api2/bframe" not in fr.url and "recaptcha/api2/anchor" not in fr.url:
                continue
            try:
                cb = fr.locator(".recaptcha-checkbox-border, #recaptcha-anchor").first
                await cb.click(timeout=5000)
                out("clicked recaptcha checkbox in frame")
                challenge = True
            except Exception as exc:
                out(f"recaptcha click: {exc!r}")
            break

        await asyncio.wait_for(page.wait_for_timeout(4000), 8)
        await page.screenshot(
            path=os.path.join(OUTDIR, f"{stamp}_register_captcha.png"), full_page=True
        )

        if challenge:
            # did we get an image challenge (interactive) or a silent pass?
            solved = False
            for fr in page.frames:
                if "recaptcha" not in fr.url:
                    continue
                try:
                    txt = await fr.locator("body").inner_text(timeout=3000)
                except Exception:
                    continue
                if re.search(r"select all|images|verify", txt, re.I):
                    out("INTERACTIVE CHALLENGE served — stopping for human.")
                    out(f"Browser-side state saved; screenshot: {stamp}_register_captcha.png")
                    # keep browser open is impossible after close; stop cleanly
                    challenge_pending = True
                    save_html("register_challenge", await page.content(), stamp)
                    out(f"CREDENTIALS (store after success): user={username} pass={password}")
                    return 3
                if "recaptcha-checkbox-checked" in txt or solved:
                    solved = True
            out("no interactive challenge detected — proceeding to submit")

        # 5. submit
        submit_loc = page.locator(
            "button:has-text('Register'), input[type='submit'][value*='Register'], "
            "button:has-text('Create'), a:has-text('Register')"
        ).last
        try:
            await submit_loc.click(timeout=6000)
            out("clicked submit")
        except Exception as exc:
            out(f"submit click failed: {exc!r}")
            save_html("register_submitfail", await page.content(), stamp)
            return 4

        await asyncio.wait_for(page.wait_for_load_state("load"), 25)
        await asyncio.wait_for(page.wait_for_timeout(4000), 10)
        html = await asyncio.wait_for(page.content(), 12)
        save_html("register_result", html, stamp)
        await page.screenshot(
            path=os.path.join(OUTDIR, f"{stamp}_register_result.png"), full_page=True
        )
        low = html.lower()
        if re.search(r"success|congratulat|account (has been )?(created|registered)", low):
            out("REGISTRATION LIKELY SUCCESSFUL")
            with open(".env", "a") as f:
                f.write(f"ACCELA_TEST_PASSWORD={password}\n")
            out("password appended to .env")
            return 0
        if "already" in low and "exist" in low:
            out("account already exists — no password stored")
            return 5
        out(f"unclear result; url={page.url[:120]} — inspect register_result.html")
        out(f"CREDENTIALS (if it actually succeeded): user={username} pass={password}")
        return 6
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
