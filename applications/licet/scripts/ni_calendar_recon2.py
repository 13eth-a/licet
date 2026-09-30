"""Recon round 2: click through the AV SPA menu into Calendaring & Inspections.

Read-only navigation: click the all-pages menu, enter "Inspections", dump the
loaded portlet URLs/frames, and look for Inspection Types / Calendars pages.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def dump_all(page, tag: str, stamp: str) -> None:
    for i, fr in enumerate(page.frames):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        if len(html) > 500:
            with open(os.path.join(OUTDIR, f"{stamp}_{tag}_f{i}.html"), "w") as f:
                f.write(html)
    await page.screenshot(
        path=os.path.join(OUTDIR, f"{stamp}_{tag}.png"), full_page=True
    )
    out(f"  dumped [{tag}] frames={len(page.frames)}")


async def click_by_text(page, text: str) -> bool:
    for sel in (f'a[aria-label="{text}"]', f'a[title="{text}"]',
                f'span.list-text:text-is("{text}")'):
        try:
            loc = page.locator(sel).first
            await loc.click(timeout=4000)
            out(f"  clicked {sel}")
            return True
        except Exception:
            continue
    return False


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

        # open the all-pages menu ("All Pages" / grid icon) then Inspections
        opened = await click_by_text(page, "Inspections")
        if not opened:
            out("direct Inspections click failed — trying menu expand first")
            for t in ("All Pages", "allpages", "Calendaring & Inspections"):
                await click_by_text(page, t)
                await asyncio.wait_for(page.wait_for_timeout(1500), 5)
            opened = await click_by_text(page, "Inspections")
        await asyncio.wait_for(page.wait_for_timeout(6000), 12)
        out(f"url after Inspections click: {page.url[:140]}")
        await dump_all(page, "inspections_portlet", stamp)

        # scan frame URLs + content for calendar/inspection-type admin hints
        for fr in page.frames:
            u = fr.url
            if any(k in u for k in ("inspection", "calendar", "portlets")):
                out(f"  frame url: {u[:140]}")
        for fr in page.frames:
            try:
                html = await asyncio.wait_for(fr.content(), 10)
            except Exception:
                continue
            hits = sorted(set(re.findall(
                r"(Inspection Types?|Calendars?|Daily Inspections?|"
                r"inspType\w*\.do|calendar\w*\.do|inspList\w*\.do)", html, re.I)))
            if hits:
                out(f"  {fr.url[-60:]}: {hits[:12]}")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
