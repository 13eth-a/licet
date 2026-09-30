"""recon phase 2: open a null island back-office record (read-only)"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
TARGET_RECORD = "BLD26-00465"
SHOTS = os.path.join("logs", "ni_backoffice")


def out(msg: str) -> None:
    print(msg, flush=True)


async def shoot(page, name: str) -> None:
    path = os.path.join(SHOTS, f"{name}.png")
    try:
        await page.screenshot(path=path)
        out(f"   [shot] {path}")
    except Exception as exc:
        out(f"   [shot FAILED] {name}: {exc}")


async def dump_frames(page, label: str) -> None:
    out(f"\n--- {label} ---")
    out(f"url: {page.url}")
    for i, fr in enumerate(page.frames):
        out(f"   frame[{i}] url={fr.url[:140]}")
    for i, fr in enumerate(page.frames):
        try:
            texts = await fr.eval_on_selector_all(
                "a, button, td, th, span, label, h1, h2, h3",
                "els => els.map(e => (e.innerText || '').trim())"
                ".filter(t => t && t.length > 1 && t.length <= 80)",
            )
            uniq: list[str] = []
            for t in texts:
                if t not in uniq:
                    uniq.append(t)
            if uniq:
                out(f"   frame[{i}] texts: {uniq[:70]}")
        except Exception:
            continue


async def find_first_visible(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def main() -> int:
    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty.")
        return 2

    os.makedirs(SHOTS, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    from solari_browser import Solari

    solari = Solari(api_key=api_key)
    out("Launching Solari cloud browser…")
    browser = await solari.launch()
    try:
        page = await browser.new_page()

        # login (same as recon1)
        await page.goto(AV_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        pwd = await find_first_visible(page, ['input[type="password"]'])
        user = await find_first_visible(
            page,
            ['input[name="username"]', 'input[id*="user" i]', 'input[type="text"]'],
        )
        if pwd is None or user is None:
            out("Login inputs not found — aborting (read-only run).")
            return 1
        await user.fill(USER)
        await pwd.fill(PASSWORD)
        submit = await find_first_visible(
            page, ['input[type="submit"]', "button[type=submit]"]
        )
        if submit is not None:
            await submit.click()
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(8000)
        out("Logged in.")
        await shoot(page, f"{stamp}_10_logged_in")

        rec_sel = f"text={TARGET_RECORD}"
        on_dashboard = True
        try:
            await page.wait_for_selector(rec_sel, timeout=30000)
        except Exception:
            on_dashboard = False
            out(f"{TARGET_RECORD} not on dashboard — opening Record search via nav")
            nav = await find_first_visible(
                page, ["a:has-text('Record')", "button:has-text('Record')"]
            )
            if nav is not None:
                try:
                    await nav.click()
                except Exception:
                    await nav.click(force=True)
                await page.wait_for_timeout(8000)
            await dump_frames(page, "after Record nav")
            await shoot(page, f"{stamp}_11_record_nav")
            s = None
            for scope in [page, *page.frames]:
                try:
                    s = await find_first_visible(
                        scope,
                        [
                            'input[placeholder*="looking for" i]',
                            'input[name="searchText"]:visible',
                            'input[name="txtSearchText"]:visible',
                            'input[id*="capSearch" i]:visible',
                            'input[type="text"]:visible',
                        ],
                    )
                except Exception:
                    continue
                if s is not None:
                    break
            if s is None:
                out("FAIL: no visible search input anywhere")
                await shoot(page, f"{stamp}_12_no_search")
                return 1
            try:
                await s.fill(TARGET_RECORD)
            except Exception:
                await s.click(force=True)
                await s.fill(TARGET_RECORD)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(8000)
            try:
                await page.wait_for_selector(rec_sel, timeout=15000)
            except Exception:
                out(f"FAIL: {TARGET_RECORD} not found in search results")
                await shoot(page, f"{stamp}_13_no_results")
                await dump_frames(page, "empty results")
                return 1

        scope = page if on_dashboard else None
        clicked = False
        if scope is not None:
            matches = scope.locator(rec_sel)
        else:
            matches = None
            for fr in page.frames:
                try:
                    if await fr.locator(rec_sel).count() > 0:
                        matches = fr.locator(rec_sel)
                        break
                except Exception:
                    continue
        if matches is None:
            out("FAIL: record link not found in any frame")
            return 1
        for i in range(await matches.count()):
            m = matches.nth(i)
            try:
                if await m.is_visible():
                    await m.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            await matches.first.click(force=True)
        await page.wait_for_timeout(10000)
        await shoot(page, f"{stamp}_12_record_detail")
        await dump_frames(page, "record detail")
        for i, fr in enumerate(page.frames):
            try:
                html = await fr.content()
                path = os.path.join(SHOTS, f"{stamp}_frame{i}.html")
                with open(path, "w") as f:
                    f.write(html)
            except Exception:
                continue
        out("   [dump] per-frame HTML saved")

        insp = await find_first_visible(
            page,
            [
                "a:has-text('Inspections')",
                "button:has-text('Inspections')",
                "td:has-text('Inspections') a",
                "text=Inspections",
            ],
        )
        if insp is None:
            for fr in page.frames:
                if fr == page.main_frame:
                    continue
                try:
                    cand = await find_first_visible(
                        fr,
                        ["a:has-text('Inspections')", "text=Inspections"],
                    )
                except Exception:
                    continue
                if cand is not None:
                    insp = cand
                    break
        if insp is not None:
            try:
                await insp.click()
                await page.wait_for_timeout(8000)
                await shoot(page, f"{stamp}_13_inspections")
                await dump_frames(page, "inspections view")
            except Exception as exc:
                out(f"   (inspections click failed: {exc})")
        else:
            out("No 'Inspections' element found to click.")

        return 0
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
