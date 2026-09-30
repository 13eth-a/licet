"""Read-only: dump one back-office record's header status + workflow tasks + inspections.

Used to *independently verify* that a back-office write (e.g. task acceptance)
actually changed the record, rather than trusting the postback.

Run:
    .venv/bin/python scripts/ni_backoffice_record_state.py --record BLD26-00469
"""
from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"


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


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    args = parser.parse_args()

    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty.")
        return 2

    from solari_browser import Solari

    solari = Solari(api_key=api_key)
    browser = await solari.launch()
    try:
        page = await browser.new_page()
        await page.goto(AV_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        user = await find_first_visible(page, ['input[name="username"]', 'input[type="text"]'])
        password = await find_first_visible(page, ['input[type="password"]'])
        if user is None or password is None:
            out("login inputs not found — aborting")
            return 1
        await user.fill(USER)
        await password.fill(PASSWORD)
        submit = await find_first_visible(page, ['input[type="submit"]', "button[type=submit]"])
        if submit is not None:
            await submit.click()
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(9000)
        out("logged in")

        url = f"{AV_URL}portlets/web/en-us/#/core/spacev360/nullisland.{args.record.lower()}"
        out(f"opening {url}")
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(20000)
        out(f"url now: {page.url}")
        try:
            top = " ".join((await page.inner_text("body")).split())
        except Exception as exc:
            top = f"<{exc}>"
        out(f"top: {top[:800]}")

        for index, frame in enumerate(page.frames):
            try:
                text = await frame.inner_text("body")
            except Exception:
                continue
            flat = " ".join(text.split())
            if not flat or len(flat) < 20:
                continue
            if any(k in flat for k in ("STATUS", "Workflow", "Inspection Type", "Task", "Issued", "Accepted")):
                out(f"frame[{index}] ({frame.url[:70]}): {flat[:900]}")
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
