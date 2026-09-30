"""Probe both scheduling surfaces (read-only): back-office Schedule form and
citizen-portal inspection side door.

No scheduling is submitted — only the forms are loaded and parsed.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
CITIZEN = "https://aca-test.accela.com/nullisland"
USER, PASSWORD = "developer", "accela"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
CAP = {"ID1": "REC26", "ID2": "00000", "ID3": "000AE"}
INSP_DETAIL = (
    "/portlets/inspection/inspectionDetailCapSpecific.do?mode=view"
    "&fromPage=inspectionDailyList&isCapFrame=Y&serviceProviderCode=NULLISLAND"
    "&capID1=REC26&capID2=00000&capID3=000AE&inspectionID=18482246"
    "&inspectionType=Mechanical+Final&scheduledDate=05-20-2026&module=Building"
)


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
        if len(html) > 1500:
            with open(os.path.join(OUTDIR, f"{stamp}_{tag}_f{i}.html"), "w") as f:
                f.write(html)
    await page.screenshot(path=os.path.join(OUTDIR, f"{stamp}_{tag}.png"),
                          full_page=True)


async def av_login(page) -> None:
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
    out("av: logged in")


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        # ---------- A) back office: inspection detail → schedule form ----------
        await av_login(page)
        await asyncio.wait_for(
            page.goto(AV_URL + INSP_DETAIL, timeout=45000, wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(4000), 10)
        await dump_frames(page, "inspDetail", stamp)
        # find Schedule button across frames
        clicked = False
        for fr in page.frames:
            try:
                cnt = await fr.locator(
                    "input[type='submit'][value*='Schedule'], "
                    "a:has-text('Schedule'), button:has-text('Schedule')"
                ).count()
                if cnt:
                    await fr.locator(
                        "input[type='submit'][value*='Schedule'], "
                        "a:has-text('Schedule'), button:has-text('Schedule')"
                    ).first.click(timeout=5000)
                    clicked = True
                    out(f"av: clicked Schedule in frame …{fr.url[-60:]}")
                    break
            except Exception:
                continue
        if clicked:
            await asyncio.wait_for(page.wait_for_timeout(4000), 10)
            await dump_frames(page, "schedForm", stamp)
            for fr in page.frames:
                try:
                    fh = await asyncio.wait_for(fr.content(), 10)
                except Exception:
                    continue
                types = re.findall(
                    r'<option[^>]*>([^<]{2,60})</option>', fh)
                types = [t.strip() for t in types if t.strip() and t.strip() != "--Select--"]
                dates = sorted(set(re.findall(
                    r"(20\d{2}-\d{2}-\d{2}|\d{2}/\d{2}/20\d{2})", fh)))[:8]
                if types or dates:
                    out(f"av schedForm frame …{fr.url[-50:]}: options={types[:12]} dates={dates}")
        else:
            out("av: no Schedule control found on detail page")

        # ---------- B) citizen side ----------
        page2 = await asyncio.wait_for(browser.new_page(), 60)
        await asyncio.wait_for(
            page2.goto(f"{CITIZEN}/Login.aspx", timeout=45000, wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page2.wait_for_timeout(3000), 8)
        lfr = None
        for fr in page2.frames:
            if "login-panel" in fr.url:
                lfr = fr
                break
        if lfr is None:
            out("citizen: login panel missing")
            return 3
        for sel, val in (("input[name='username']",
                          os.environ["ACCELA_TEST_USERNAME"].strip()),
                         ("input[name='password']",
                          os.environ["ACCELA_TEST_PASSWORD"].strip())):
            loc = lfr.locator(sel).first
            try:
                await loc.fill(val, timeout=5000)
            except Exception:
                await loc.focus(timeout=5000)
                await page2.keyboard.type(val, delay=30)
        await lfr.locator("button").first.click(timeout=6000)
        await asyncio.wait_for(page2.wait_for_load_state("load"), 25)
        await asyncio.wait_for(page2.wait_for_timeout(3000), 8)
        out(f"citizen: logged in → {page2.url[:100]}")

        await asyncio.wait_for(
            page2.goto(
                f"{CITIZEN}/Cap/CapHome.aspx?IsToShowInspection=yes&module=Building",
                timeout=45000, wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page2.wait_for_timeout(3000), 8)
        await dump_frames(page2, "citizen_insp", stamp)
        html = await asyncio.wait_for(page2.content(), 12)
        # record rows with schedule actions?
        rows = re.findall(r"capID1=\d*[A-Za-z]*[^&\"']*", html)[:6]
        out(f"citizen inspection page: {len(html)}B; capID mentions: {rows}")
        has_sched = bool(re.search(r"Schedule", html, re.I))
        out(f"citizen: 'Schedule' text present: {has_sched}")
        await page2.screenshot(path=os.path.join(OUTDIR, f"{stamp}_citizen_insp.png"))
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
