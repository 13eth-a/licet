"""Probe capDetail.do with explicit capID params (read-only, single record)."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
SHOTS = os.path.join("logs", "ni_backoffice", "inventory")
CAP = os.environ.get("PROBE_CAP", "REC26-00000-000BU")
MODULE = "Building"


async def main() -> int:
    from solari_browser import Solari

    api_key = os.environ["SOLARI_API_KEY"].strip()
    solari = Solari(api_key=api_key)
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        print("logged in")

        c1, c2, c3 = CAP.split("-")
        url = (
            f"{AV_URL}portlets/cap/capDetail.do?mode=view"
            f"&serviceProviderCode=NULLISLAND&ID1={c1}&ID2={c2}&ID3={c3}"
            f"&isRedirect=false&module={MODULE}"
        )
        await asyncio.wait_for(
            page.goto(url, timeout=45000, wait_until="domcontentloaded"), 55
        )
        await asyncio.wait_for(page.wait_for_timeout(4000), 10)
        print("url:", page.url)
        best = ""
        for i, fr in enumerate(page.frames):
            try:
                html = await asyncio.wait_for(fr.content(), 15)
            except Exception:
                continue
            path = os.path.join(SHOTS, f"{stamp}_detail_frame{i}.html")
            with open(path, "w") as f:
                f.write(html)
            if len(html) > len(best):
                best = html
            print(f"frame[{i}] {len(html):>8} bytes → {path}")

        text = re.sub(r"<script.*?</script>", " ", best, flags=re.S)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = re.sub(r"&nbsp;?", " ", text)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        uniq: list[str] = []
        for l in lines:
            if not uniq or uniq[-1] != l:
                uniq.append(l)
        print("\n".join(uniq[:160]))
        await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_detail.png"))
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
