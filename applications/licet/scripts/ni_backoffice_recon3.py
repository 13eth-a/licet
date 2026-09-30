"""recon phase 3: find null island records with inspections (read only)"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
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


async def dump_frames(page, label: str, save_html: bool = True) -> None:
    out(f"\n--- {label} ---")
    out(f"url: {page.url}")
    stamp = label.replace(" ", "_")[:24]
    for i, fr in enumerate(page.frames):
        out(f"   frame[{i}] url={fr.url[:150]}")
        try:
            texts = await fr.eval_on_selector_all(
                "a, button, td, th, span, label, h1, h2, h3, option",
                "els => els.map(e => (e.innerText || '').trim())"
                ".filter(t => t && t.length > 1 && t.length <= 80)",
            )
            uniq: list[str] = []
            for t in texts:
                if t not in uniq:
                    uniq.append(t)
            if uniq:
                out(f"   frame[{i}] texts: {uniq[:80]}")
        except Exception:
            continue
        if save_html:
            try:
                html = await fr.content()
                with open(os.path.join(SHOTS, f"{stamp}_frame{i}.html"), "w") as f:
                    f.write(html)
            except Exception:
                pass


async def find_first_visible(scope, selectors: list[str]):
    for sel in selectors:
        try:
            loc = scope.locator(sel).first
            if await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def click_in_any_frame(page, selectors: list[str]) -> bool:
    """try a click in the main document first, then every child frame"""
    try:
        loc = await find_first_visible(page, selectors)
        if loc is not None:
            await loc.click()
            return True
    except Exception:
        pass
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            loc = await find_first_visible(fr, selectors)
            if loc is not None:
                await loc.click()
                return True
        except Exception:
            continue
    return False


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

        await page.goto(AV_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        pwd = await find_first_visible(page, ['input[type="password"]'])
        user = await find_first_visible(
            page, ['input[name="username"]', 'input[type="text"]']
        )
        if pwd is None or user is None:
            out("Login inputs not found — aborting.")
            return 1
        await user.fill(USER)
        await pwd.fill(PASSWORD)
        await (await find_first_visible(page, ['button[type=submit]'])).click()
        await page.wait_for_timeout(10000)
        out("Logged in.")
        await shoot(page, f"{stamp}_20_logged_in")

        clicked = await click_in_any_frame(
            page,
            [
                "a:has-text('Inspections')",
                "button:has-text('Inspections')",
            ],
        )
        out(f"\nInspections nav clicked: {clicked}")
        await page.wait_for_timeout(10000)
        await shoot(page, f"{stamp}_21_inspections_portlet")
        await dump_frames(page, "inspections portlet")

        for fr in page.frames:
            if "capSearch.do" in fr.url and "pageNo=" not in fr.url:
                try:
                    rows = await fr.eval_on_selector_all(
                        "table tr",
                        """els => els.map(r =>
                             Array.from(r.cells || []).map(c =>
                               (c.innerText || '').trim().slice(0, 30)
                             ).join(' | ')
                           ).filter(r => r.length > 5)""",
                    )
                    out(f"\n=== Record grid rows ({len(rows)}) ===")
                    for r in rows[:15]:
                        out(f"   {r[:180]}")
                except Exception as exc:
                    out(f"   (grid dump failed: {exc})")

        return 0
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
