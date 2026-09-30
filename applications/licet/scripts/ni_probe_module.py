"""probe module switching in the null island record grid (read-only)"""
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


def out(m: str) -> None:
    print(m, flush=True)


async def grid_frame(page):
    for fr in page.frames:
        if "capSearch.do" in fr.url:
            return fr
    return None


async def row_count(page) -> tuple[int, set[str]]:
    fr = await grid_frame(page)
    if fr is None:
        return 0, set()
    try:
        html = await asyncio.wait_for(fr.content(), 15)
    except Exception:
        return 0, set()
    ids = set(re.findall(r'value="((?:BLD|REC|CP|PRJ|CMP)\d{2}-\d{5})"', html))
    rows = len(re.findall(r'name="value\(CAPID1,\d+\)"', html))
    return rows, ids


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
        out("logged in")

        fr = await grid_frame(page)
        out(f"grid frame: {fr.url[:130] if fr else None}")
        sel = fr.locator('select[name="moduleList"]')
        out(f"moduleList count: {await sel.count()}")
        await asyncio.wait_for(sel.first.select_option("Planning"), 15)
        await asyncio.wait_for(page.wait_for_timeout(6000), 12)
        fr2 = await grid_frame(page)
        out(f"after select → frame url: {fr2.url[:130] if fr2 else None}")
        rows, ids = await row_count(page)
        out(f"rows={rows} ids={sorted(ids)[:6]}")
        await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_moduleA.png"))

        url = (
            f"{AV_URL}portlets/cap/capSearch.do?pageNo=1&totalPages=1"
            f"&column=altID&module=Planning&spaceName=spaces.nullisland.record"
            f"&isGeneralCAP=Y"
        )
        await asyncio.wait_for(
            page.goto(url, timeout=45000, wait_until="domcontentloaded"), 55
        )
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        rows, ids = await row_count(page)
        fr3 = await grid_frame(page)
        out(f"B: rows={rows} ids={sorted(ids)[:6]} frame={fr3.url[:110] if fr3 else None}")
        denied = False
        for f4 in page.frames:
            try:
                h = await asyncio.wait_for(f4.content(), 10)
                if "Access is denied" in h:
                    denied = True
            except Exception:
                continue
        out(f"B: access-denied={denied}")
        await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_moduleB.png"))
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
