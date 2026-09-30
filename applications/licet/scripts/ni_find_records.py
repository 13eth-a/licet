"""one off solari exploration: discover usable test records on null island"""

from __future__ import annotations

import asyncio
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

BASE = "https://aca-test.accela.com/nullisland"
MODULES = ["Building", "Enforcement"]
# result rows on aca grids are postback links, not capid hrefs count any anchor inside the results area
# and look for text markers instead
START = "#ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate"
END = "#ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate"
STREET = 'input[id$="txtGSStreetName"]'
PERMIT = 'input[id$="txtGSPermitNumber"]'
SEARCH = "a#btnSearch"
MASK_CSS = ".ACA_MaskDiv, #divGlobalLoadingMask { display: none !important; }"
RESULT_LINKS = 'a[href*="capID1="], a[onclick*="capID1"]'
CAPID_RE = re.compile(
    r"capID1=([^&'\"<>]+?)(?:&amp;|&)capID2=([^&'\"<>]+?)(?:&amp;|&)capID3=([^&'\"<>&#]+)"
)


async def settle(page, seconds: float = 10.0) -> None:
    try:
        await page.wait_for_load_state("load", timeout=seconds * 1000)
    except Exception:
        pass


async def unmask(page) -> None:
    try:
        await page.add_style_tag(content=MASK_CSS)
    except Exception:
        pass


async def clear_text_fields(page) -> None:
    await page.evaluate(
        "() => document.querySelectorAll('#PlaceHolderMain input[type=\"text\"]')"
        ".forEach(e => { if (!e.readOnly && !e.disabled) e.value = ''; });"
    )


async def fill_if_present(page, selector: str, value: str) -> bool:
    loc = page.locator(selector).first
    if not await page.locator(selector).count():
        return False
    try:
        await loc.fill(value)
        return True
    except Exception:
        return False


async def submit(page) -> str:
    try:
        await page.click(SEARCH, timeout=4000)
        return "click"
    except Exception:
        try:
            await page.click(SEARCH, force=True, timeout=4000)
            return "force-click"
        except Exception:
            return "none"


async def main() -> int:
    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        print("SOLARI_API_KEY is empty.")
        return 2

    from solari_browser import Solari

    found: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    solari = Solari(api_key=api_key)
    browser = await solari.launch()
    try:
        page = await browser.new_page()
        for module in MODULES:
            url = f"{BASE}/Cap/CapHome.aspx?TabName=Home&module={module}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                print(f"{module}: goto failed — {exc!r}")
                continue
            await settle(page)
            await unmask(page)
            await fill_if_present(page, START, "01/01/1990")
            await fill_if_present(page, END, "12/31/2035")

            strategies = [
                ("street=Main", STREET, "Main"),
                ("street=Test", STREET, "Test"),
                ("street=1", STREET, "1"),
                ("permit=%", PERMIT, "%"),
                ("permit=A", PERMIT, "A"),
            ]
            for label, selector, term in strategies:
                if not await page.locator(selector).count():
                    continue
                await clear_text_fields(page)
                loc = page.locator(selector).first
                try:
                    await loc.click(timeout=4000)
                    await loc.press_sequentially(term, delay=30)
                except Exception:
                    continue
                path = await submit(page)
                if path == "none":
                    print(f"{module}/{label}: could not submit")
                    break
                await settle(page, 12)
                await unmask(page)
                content = await page.content()
                ids = sorted(set(CAPID_RE.findall(content)))
                body = await page.locator("body").inner_text()
                markers = re.findall(
                    r"(Search Result[s]?|Records? Found|\b1 - \d+|Record #|"
                    r"no records? (?:found|match)|returned no results)",
                    body, re.I,
                )
                grid_links = await page.eval_on_selector_all(
                    '#PlaceHolderMain a',
                    "els => els.map(e => (e.getAttribute('onclick') || '') +"
                    "(e.getAttribute('href') || '')).filter(t =>"
                    "t.includes('capID1') || t.includes('__doPostBack'))"
                )
                if ids or ("Search Result" in body or "Found" in body and grid_links):
                    if ids:
                        found[(module, label)] = ids
                        print(f"{module}/{label}: {len(ids)} records: "
                              + ", ".join(f"{a}-{b}-{c}" for a, b, c in ids[:6]))
                        break
                    print(f"{module}/{label}: results area rendered "
                          f"(markers={markers[:3]}, grid-links={len(grid_links)}) "
                          f"but no capID parsed — dumping diagnostics")
                    tag = f"{module}_{label.replace('=', '').replace('%', 'wild')}"
                    await page.screenshot(path=f"logs/solari_verify/ni_{tag}.png")
                    with open(f"logs/solari_verify/ni_{tag}.html", "w") as f:
                        f.write(content)
                    i = body.lower().find("search result")
                    print("   excerpt:", repr(body[max(0, i - 50): i + 400]))
                    break
                print(f"{module}/{label}: no results (markers={markers[:2]}, "
                      f"grid-links={len(grid_links)}, path={path})")
    finally:
        await browser.close()

    if not found:
        print("\nNo records found with any strategy — next step is registering")
        print("the public-user account (My Records) or asking Accela for seed data.")
        return 1

    (module, label), ids = next(iter(found.items()))
    c1, c2, c3 = ids[0]
    record_url = (
        f"{BASE}/?Module={module}&TabName={module}"
        f"&capID1={c1}&capID2={c2}&capID3={c3}&agencyCode=nullisland"
    )
    solari2 = Solari(api_key=api_key)
    browser2 = await solari2.launch()
    try:
        page = await browser2.new_page()
        await page.goto(record_url, wait_until="domcontentloaded", timeout=45000)
        await settle(page, 15)
        await unmask(page)
        body = await page.locator("body").inner_text()
        print("\n=== RECORD DETAIL (first hit) ===")
        print("url:", page.url)
        print("record id visible:", f"{c1}-{c2}-{c3}" in body or c1 in body)
        print("body excerpt:\n", body[:900])
        stamp = "ni_record"
        await page.screenshot(path=f"logs/solari_verify/{stamp}.png")
        print("screenshot: logs/solari_verify/ni_record.png")
    finally:
        await browser2.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
