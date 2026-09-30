"""read-only recon of the null island back office (classic admin) via solari"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
AGENCY, USER, PASSWORD = "nullisland", "developer", "accela"
SHOTS = os.path.join("logs", "ni_backoffice")


def out(msg: str) -> None:
    print(msg, flush=True)


async def dump_state(page, label: str) -> None:
    out(f"\n--- {label} ---")
    out(f"url:   {page.url}")
    try:
        out(f"title: {await page.title()}")
    except Exception:
        pass
    try:
        controls = await page.eval_on_selector_all(
            "input, select, button, iframe",
            """els => els.slice(0, 60).map(e => ({
                 tag: e.tagName,
                 type: e.type || '',
                 id: e.id || '',
                 name: e.name || '',
                 visible: !!(e.offsetWidth || e.offsetHeight),
                 src: e.tagName === 'IFRAME' ? (e.src || '').slice(0, 90) : undefined,
               }))""",
        )
        for c in controls:
            out(f"   {c}")
    except Exception as exc:
        out(f"   (control dump failed: {exc})")
    try:
        texts = await page.eval_on_selector_all(
            "a, button",
            "els => els.map(e => (e.innerText || '').trim()).filter(t => t && t.length <= 45)",
        )
        uniq: list[str] = []
        for t in texts:
            if t not in uniq:
                uniq.append(t)
        out(f"   link/button texts: {uniq[:60]}")
    except Exception as exc:
        out(f"   (text dump failed: {exc})")


async def shoot(page, name: str) -> None:
    path = os.path.join(SHOTS, f"{name}.png")
    try:
        await page.screenshot(path=path)
        out(f"   [shot] {path}")
    except Exception as exc:
        out(f"   [shot FAILED] {name}: {exc}")


async def find_first_visible(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def main() -> int:
    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty — paste your slr_live_ key into .env first.")
        return 2

    os.makedirs(SHOTS, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    from solari_browser import Solari

    solari = Solari(api_key=api_key)
    out("Launching Solari cloud browser…")
    browser = await solari.launch()
    try:
        page = await browser.new_page()

        await page.goto(AV_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        await dump_state(page, "login page")
        await shoot(page, f"{stamp}_01_login")
        html = await page.content()
        with open(os.path.join(SHOTS, "login_dump.html"), "w") as f:
            f.write(html)
        out(f"   [dump] {os.path.join(SHOTS, 'login_dump.html')}")

        pwd = await find_first_visible(
            page,
            ['input[type="password"]'],
        )
        if pwd is None:
            out("\nNo visible password field — login form is JS-rendered or inside an iframe.")
            for i, fr in enumerate(page.frames):
                out(f"   frame[{i}] url={fr.url[:120]}")
            return 1

        user = await find_first_visible(
            page,
            [
                'input[id*="user" i]',
                'input[name*="user" i]',
                'input[type="text"]',
                'input[type="email"]',
            ],
        )
        agency = await find_first_visible(
            page,
            [
                'input[id*="agency" i]',
                'input[name*="agency" i]',
                'select[id*="agency" i]',
                'select[name*="agency" i]',
            ],
        )
        out(f"\ninputs → agency={agency is not None}, user={user is not None}, pwd=True")

        if agency is not None and (await agency.get_attribute("tagname")) != "SELECT":
            tag = await agency.evaluate("e => e.tagName")
            if tag == "SELECT":
                await agency.select_option(AGENCY)
            else:
                await agency.fill(AGENCY)
        if user is not None:
            await user.fill(USER)
        await pwd.fill(PASSWORD)
        await shoot(page, f"{stamp}_02_filled")

        submit = await find_first_visible(
            page,
            [
                'input[type="submit"]',
                "button:has-text('Login')",
                "a:has-text('Login')",
                "input[value*='ogin' i]",
            ],
        )
        if submit is not None:
            await submit.click()
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(8000)
        await dump_state(page, "after login")
        await shoot(page, f"{stamp}_03_after_login")

        html = await page.content()
        with open(os.path.join(SHOTS, "after_login_dump.html"), "w") as f:
            f.write(html)
        out(f"   [dump] {os.path.join(SHOTS, 'after_login_dump.html')}")

        for i, fr in enumerate(page.frames):
            out(f"   frame[{i}] url={fr.url[:140]}")

        return 0
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
