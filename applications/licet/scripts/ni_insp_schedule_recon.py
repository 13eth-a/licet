"""recon: does ni have usable inspection calendars? (read only)"""
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
CAP = {"ID1": "REC26", "ID2": "00000", "ID3": "000AE"}


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def dump_frames(page, tag: str, stamp: str) -> dict[int, str]:
    saved = {}
    for i, fr in enumerate(page.frames):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        if len(html) > 1500:
            path = os.path.join(OUTDIR, f"{stamp}_{tag}_f{i}.html")
            with open(path, "w") as f:
                f.write(html)
            saved[i] = path
    await page.screenshot(path=os.path.join(OUTDIR, f"{stamp}_{tag}.png"),
                          full_page=True)
    return saved


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

        qs = (f"mode=tabSummary&serviceProviderCode=NULLISLAND"
              f"&ID1={CAP['ID1']}&ID2={CAP['ID2']}&ID3={CAP['ID3']}"
              f"&requireNotice=YES&clearForm=clearForm&module=Building"
              f"&isFromCapList=true&isGeneralCAP=Y")
        await asyncio.wait_for(
            page.goto(f"{AV_URL}portlets/cap/capsummary/CapTabSummary.do?{qs}",
                      timeout=45000, wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(5000), 12)
        out(f"record page: {page.url[:120]} frames={len(page.frames)}")

        links: dict[str, str] = {}
        for fr in page.frames:
            try:
                fh = await asyncio.wait_for(fr.content(), 10)
            except Exception:
                continue
            for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', fh, re.S | re.I):
                href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
                text = re.sub(r"\s+", " ", text).strip()
                if text and len(text) < 40 and not href.startswith(("javascript", "#")):
                    links[text] = href
        out("links: " + ", ".join(sorted(links)[:25]))
        insp = {t: h for t, h in links.items()
                if re.search(r"inspect|sched", t, re.I)}
        out(f"inspection-ish links: {list(insp)}")
        with open(os.path.join(OUTDIR, f"{stamp}_reclinks.txt"), "w") as f:
            for t, h in sorted(links.items()):
                f.write(f"{t}\t{h}\n")

        target = None
        for t, h in insp.items():
            target = h if h.startswith("http") else AV_URL + h.lstrip("/")
            out(f"trying section: {t} → {target[:130]}")
            await asyncio.wait_for(
                page.goto(target, timeout=45000, wait_until="domcontentloaded"), 55
            )
            await asyncio.wait_for(page.wait_for_timeout(4000), 10)
            await dump_frames(page, "inspSection", stamp)
            for fr in page.frames:
                try:
                    fh = await asyncio.wait_for(fr.content(), 10)
                except Exception:
                    continue
                if re.search(r"schedule( an)? inspection", fh, re.I):
                    out(f"  schedule UI present in frame: {fr.url[-70:]}")
        if not insp:
            out("no inspection link found on tab summary — dumping frames for manual scan")
            await dump_frames(page, "recSummary", stamp)
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
