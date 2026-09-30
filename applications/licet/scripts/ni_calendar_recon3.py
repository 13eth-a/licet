"""Recon round 3: reach the daily Inspections portlet via favorites bar.

Read-only. Click a.favorite-text[aria-label=Inspections] (visible favorites
bar), dump the portlet; also probe the spacev360 inspection space URL.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
INSPEC_SPACE = AV_URL + "portlets/web/en-us/#/core/spacev360/nullisland.inspection"
USER, PASSWORD = "developer", "accela"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def dump_frames(page, tag: str, stamp: str) -> None:
    for i, fr in enumerate(page.frames):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        if len(html) > 800:
            with open(os.path.join(OUTDIR, f"{stamp}_{tag}_f{i}.html"), "w") as f:
                f.write(html)
            hits = sorted(set(re.findall(
                r"(inspection types?|calendars?|daily inspection|am\/pm|"
                r"schedul\w+)", html, re.I)))[:8]
            out(f"  f{i}: len={len(html)} url=…{fr.url[-70:]} hits={hits}")


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        await asyncio.wait_for(
            page.goto(AV_URL, timeout=60000, wait_until="domcontentloaded"), 70
        )
        await asyncio.wait_for(page.wait_for_timeout(5000), 15)
        user = page.locator('input[name="username"]').first
        if not await user.is_visible():
            user = page.locator('input[type="text"]').first
        await user.fill(USER)
        await page.locator('input[type="password"]').first.fill(PASSWORD)
        try:
            await page.locator('input[type="submit"]').first.click(timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")
        await asyncio.wait_for(page.wait_for_timeout(10000), 20)
        out("logged in")

        # A) favorites-bar click
        try:
            await page.locator('a.favorite-text[aria-label="Inspections"]').first.click(
                timeout=6000
            )
            await asyncio.wait_for(page.wait_for_timeout(6000), 12)
            out(f"A) after favorites click: url={page.url[:130]}")
            await dump_frames(page, "inspA", stamp)
        except Exception as exc:
            out(f"A) favorites click failed: {exc!r}")

        # B) direct space URL for inspections
        await asyncio.wait_for(
            page.goto(INSPEC_SPACE, timeout=45000, wait_until="domcontentloaded"), 55
        )
        await asyncio.wait_for(page.wait_for_timeout(6000), 12)
        out(f"B) after space URL: url={page.url[:130]}")
        await dump_frames(page, "inspB", stamp)
        await page.screenshot(path=os.path.join(OUTDIR, f"{stamp}_inspB.png"),
                              full_page=True)
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
