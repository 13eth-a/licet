"""Find the real apply-flow URL on NI's citizen portal (read-only).

Scrape Default.aspx for all links, filter apply-ish ones, then probe a few
candidate URLs and report which one renders the record-type chooser.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

CITIZEN = "https://aca-test.accela.com/nullisland"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
CANDIDATES = [
    "/Cap/CapWiz.aspx?module=Building",
    "/Cap/CapWiz.aspx",
    "/Cap/ApplyWiz.aspx?module=Building",
    "/Cap/CapHome.aspx?TabName=Home&module=Building",
]


def out(m: str) -> None:
    print(m, flush=True)


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Default.aspx", timeout=45000,
                      wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        html = await asyncio.wait_for(page.content(), 15)
        path = os.path.join(OUTDIR, f"{stamp}_home.html")
        with open(path, "w") as f:
            f.write(html)
        hrefs = re.findall(r'href="([^"]+)"[^>]*>([^<]{0,80})', html)
        out("=== home links (apply/permit/wiz-ish) ===")
        seen = set()
        for href, text in hrefs:
            blob = (href + " " + text).lower()
            if re.search(r"apply|wiz|permit|create|cap", blob) and href not in seen:
                seen.add(href)
                out(f"  {text.strip()[:40]!r} → {href[:110]}")

        out("\n=== candidate probes ===")
        for cand in CANDIDATES:
            url = f"{CITIZEN}{cand}"
            try:
                await asyncio.wait_for(
                    page.goto(url, timeout=35000, wait_until="domcontentloaded"), 45
                )
                await asyncio.wait_for(page.wait_for_timeout(2000), 8)
                chtml = await asyncio.wait_for(page.content(), 10)
                n_sel = chtml.count("<select")
                n_opt = chtml.count("<option")
                is_404 = "404" in chtml[:600] and "not found" in chtml.lower()[:800]
                title = re.search(r"<title>([^<]*)</title>", chtml)
                out(f"  {cand[:60]:60s} selects={n_sel:3d} options={n_opt:3d} "
                    f"404={is_404} title={title.group(1)[:40] if title else '?'}")
                if n_opt and not is_404:
                    p2 = os.path.join(OUTDIR, f"{stamp}_hit{cand.replace('/', '_').replace('?', '_').replace('=', '_').replace('&', '_')}.html")
                    with open(p2, "w") as f:
                        f.write(chtml)
                    out(f"    ↑ SAVED {p2}")
            except Exception as exc:
                out(f"  {cand[:60]:60s} ERR {exc!r}")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
