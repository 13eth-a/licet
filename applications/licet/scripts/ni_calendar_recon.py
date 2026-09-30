"""Recon NI back-office admin menu for Inspection/Calendar admin pages.

Read-only: logs in, dumps the dashboard navigation/portlet menu HTML, and
greps it for inspection/calendar references. No settings are changed.
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


async def login(page) -> None:
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


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        await login(page)
        html = await asyncio.wait_for(page.content(), 15)
        with open(os.path.join(OUTDIR, f"{stamp}_av_home.html"), "w") as f:
            f.write(html)
        out(f"home html {len(html)} bytes")

        # collect nav links (top-level and frame-hosted)
        links: dict[str, str] = {}
        for fr in page.frames:
            try:
                fh = await asyncio.wait_for(fr.content(), 10)
            except Exception:
                continue
            for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', fh, re.S | re.I):
                href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
                text = re.sub(r"\s+", " ", text).strip()
                if not text or len(text) > 60 or href.startswith(("javascript", "#")):
                    continue
                links[text] = href
        with open(os.path.join(OUTDIR, f"{stamp}_av_links.txt"), "w") as f:
            for t, h in sorted(links.items()):
                f.write(f"{t}\t{h}\n")
        out(f"collected {len(links)} links → av_links.txt")
        for t, h in sorted(links.items()):
            if re.search(r"inspect|calendar|daily|admin|setting|automatio", t, re.I):
                out(f"  {t} → {h[:110]}")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
