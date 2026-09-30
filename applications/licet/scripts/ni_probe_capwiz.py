"""Probe CapWiz structure for one module: frames, selects, screenshots."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

CITIZEN = "https://aca-test.accela.com/nullisland"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Cap/CapWiz.do?module=Building", timeout=45000,
                      wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(4000), 10)
        print(f"final url: {page.url}")
        print(f"frames: {len(page.frames)}")
        for i, fr in enumerate(page.frames):
            try:
                html = await asyncio.wait_for(fr.content(), 10)
            except Exception as exc:
                print(f"  frame {i}: {fr.url[:100]} — content err {exc!r}")
                continue
            n_sel = html.count("<select")
            n_opt = html.count("<option")
            print(f"  frame {i}: len={len(html)} selects={n_sel} options={n_opt} url={fr.url[:100]}")
            path = os.path.join(OUTDIR, f"{stamp}_capwiz_frame{i}.html")
            with open(path, "w") as f:
                f.write(html)
        await page.screenshot(path=os.path.join(OUTDIR, f"{stamp}_capwiz.png"), full_page=True)
        print("screenshot saved")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
