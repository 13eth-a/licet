"""Verify the NI public-user account: log in via Solari, check session.

Uses ACCELA_TEST_USERNAME/PASSWORD from .env (created 2026-09-19 via
CommunityView/account/new). Read-only: login + navigation checks only, no
scheduling/applying.

Checks:
  1. login-panel iframe (CommunityView/login-panel) accepts credentials
  2. logged-in indicators appear (Sign Out / account name / My Records)
  3. auth survives navigation (home → CapHome) — the pending checklist item
  4. accela session cookies captured

Run:  .venv/bin/python scripts/ni_login_verify.py
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

CITIZEN = "https://aca-test.accela.com/nullisland"
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
USER = os.environ.get("ACCELA_TEST_USERNAME", "").strip()
PWD = os.environ.get("ACCELA_TEST_PASSWORD", "").strip()


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def login_frame(page):
    for fr in page.frames:
        if "login-panel" in fr.url:
            return fr
    return None


async def logged_in_markers(page) -> list[str]:
    """Scan all frames' text for logged-in indicators."""
    markers = []
    for fr in page.frames:
        try:
            txt = await asyncio.wait_for(fr.locator("body").inner_text(), 6)
        except Exception:
            continue
        for pat in ("Sign Out", "Log Out", "Logout", "My Records", "My Account",
                    "Welcome"):
            if pat.lower() in txt.lower() and pat not in markers:
                markers.append(pat)
    return markers


async def main() -> int:
    from solari_browser import Solari

    if not USER or not PWD:
        out("ACCELA_TEST_USERNAME/PASSWORD missing in .env")
        return 2

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    results: dict[str, object] = {}
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        # 1. login page
        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Login.aspx", timeout=45000,
                      wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(3500), 10)
        fr = await login_frame(page)
        if fr is None:
            out("FAIL: login-panel iframe not found")
            return 3
        out(f"login panel: {fr.url[:100]}")

        # 2. fill credentials (recon: inputs have name= attrs, not ids)
        for sel, val in (("input[name='username']", USER),
                         ("input[name='password']", PWD),
                         ("input[type='password']", PWD)):
            if await fr.locator(sel).count() == 0:
                continue
            loc = fr.locator(sel).first
            try:
                await loc.fill(val, timeout=5000)
                out(f"filled {sel}")
            except Exception:
                await loc.focus(timeout=5000)
                await page.keyboard.type(val, delay=40)
                out(f"typed {sel}")
            if "password" in sel:
                break

        # 3. submit — the panel has one button; try click then Enter fallback
        try:
            await fr.locator("button").first.click(timeout=6000)
            out("clicked login button")
        except Exception:
            await fr.locator("input[name='password']").first.press("Enter")
            out("pressed Enter on password")
        await asyncio.wait_for(page.wait_for_load_state("load"), 25)
        await asyncio.wait_for(page.wait_for_timeout(4000), 10)
        out(f"after login url: {page.url[:120]}")
        results["post_login_url"] = page.url

        markers = await logged_in_markers(page)
        out(f"logged-in markers: {markers}")
        results["markers"] = markers
        await page.screenshot(
            path=os.path.join(OUTDIR, f"{stamp}_login_after.png"), full_page=True
        )
        if not markers:
            out("FAIL: no logged-in markers — credentials may be wrong or verify pending")
            return 4

        # 4. auth survives navigation → CapHome (the pending checklist item)
        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Cap/CapHome.aspx?TabName=Home&module=Building",
                      timeout=45000, wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(2500), 8)
        markers2 = await logged_in_markers(page)
        out(f"markers on CapHome after navigation: {markers2}")
        results["markers_caphome"] = markers2
        cookies = await page.context.cookies()
        accela = [c["name"] for c in cookies if "accela" in c.get("domain", "")]
        out(f"accela cookies: {len(accela)}")
        results["cookie_count"] = len(accela)
        await page.screenshot(
            path=os.path.join(OUTDIR, f"{stamp}_caphome_loggedin.png"), full_page=True
        )

        ok = bool(markers2) and len(accela) > 0
        out("LOGIN VERIFIED — auth survives navigation ✅" if ok
            else "PARTIAL: logged in but session did not survive navigation")
        return 0 if ok else 5
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
