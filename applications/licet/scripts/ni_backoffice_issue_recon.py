"""read only: does the back office expose an *issue* action for our records?"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
SHOTS = os.path.join("logs", "ni_backoffice")
ISSUE_WORDS = ("issue", "issued", "workflow", "work flow", "accept", "status",
               "application acceptance", "application submittal")


def out(message: str) -> None:
    print(message, flush=True)


async def find_first_visible(scope, selectors: list[str]):
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def click_in_any_frame(page, selectors: list[str], text: str | None = None) -> bool:
    scopes = [page, *page.frames]
    for scope in scopes:
        try:
            locator = scope.get_by_text(text, exact=False).first if text else None
            if locator is None:
                locator = await find_first_visible(scope, selectors)
            if locator is not None and await locator.is_visible():
                await locator.click()
                return True
        except Exception:
            continue
    return False


async def dump(page, stamp: str, label: str) -> list[str]:
    out(f"\n--- {label} (url={page.url}) ---")
    collected: list[str] = []
    for index, frame in enumerate(page.frames):
        try:
            texts = await frame.eval_on_selector_all(
                "a, button, td, th, span, label, h1, h2, h3, option, input",
                "els => els.map(e => (e.innerText || e.value || '').trim())"
                ".filter(t => t && t.length > 1 && t.length <= 90)",
            )
        except Exception:
            continue
        unique: list[str] = []
        for value in texts:
            if value not in unique:
                unique.append(value)
        collected.extend(unique)
        try:
            html = await frame.content()
            path = os.path.join(SHOTS, f"{stamp}_{label}_f{index}.html")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(html)
        except Exception:
            pass
    hits = sorted({value for value in collected if any(word in value.lower() for word in ISSUE_WORDS)})
    out(f"  issue/workflow-related text ({len(hits)}): {hits[:40]}")
    return collected


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default="BLD26-00469")
    args = parser.parse_args()

    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty.")
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
        user = await find_first_visible(page, ['input[name="username"]', 'input[id*="user" i]', 'input[type="text"]'])
        password = await find_first_visible(page, ['input[type="password"]'])
        if user is None or password is None:
            out("login inputs not found — aborting (read-only run)")
            return 1
        await user.fill(USER)
        await password.fill(PASSWORD)
        submit = await find_first_visible(page, ['input[type="submit"]', "button[type=submit]"])
        if submit is not None:
            await submit.click()
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(8000)
        out("logged in")
        await dump(page, stamp, "dashboard")

        # open the record space (read only navigation)
        clicked = await click_in_any_frame(page, ["a:has-text('Record')"], text="Record")
        out(f"Record nav clicked: {clicked}")
        await page.wait_for_timeout(8000)

        # load more of the record grid if the record is not on the first page
        for _ in range(4):
            if await click_in_any_frame(page, [], text=args.record):
                break
            await click_in_any_frame(page, ["button:has-text('Load More')"], text="Load More")
            await page.wait_for_timeout(4000)
        await page.wait_for_timeout(5000)
        await dump(page, stamp, f"record_{args.record.replace('-', '_')}")
        try:
            await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_issue_recon.png"))
        except Exception:
            pass
    finally:
        try:
            await solari.close(browser)
        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
