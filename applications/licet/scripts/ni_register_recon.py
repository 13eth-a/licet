"""Recon the NI citizen-portal account-registration flow (read-only).

docs/accela_ui_map.md says Login.aspx ships zero password fields in raw HTML
(CivicId SSO rendered client-side, likely iframe/popup). This pass only maps
the terrain: opens Login.aspx, dumps links/buttons, clicks "Register" if
found, and dumps whatever form appears (any frame). No fields are filled,
nothing is submitted.

Run:  .venv/bin/python scripts/ni_register_recon.py
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


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def save_html(tag: str, html: str, stamp: str) -> None:
    path = os.path.join(OUTDIR, f"{stamp}_{tag}.html")
    with open(path, "w") as f:
        f.write(html)
    out(f"  saved {os.path.basename(path)} ({len(html)} bytes)")


def summarize_form(tag: str, html: str) -> None:
    inputs = re.findall(
        r"<input[^>]*type=[\"'](text|email|password|hidden)[\"'][^>]*>", html, re.I
    )
    fields = re.findall(
        r"<input[^>]*name=[\"']([^\"']+)[\"'][^>]*>", html, re.I
    )
    labels = re.findall(r"<label[^>]*>([^<]{1,60})</label>", html)
    selects = re.findall(r"<select[^>]*name=[\"']([^\"']+)[\"']", html, re.I)
    buttons = re.findall(
        r"<(?:button|input)[^>]*(?:type=[\"'](?:submit|button)[\"']|value=[\"'][^\"']*(?:continue|register|create|submit)[^\"']*[\"'])[^>]*>",
        html, re.I,
    )
    out(f"  [{tag}] inputs={len(inputs)} text-like, names={fields[:12]}")
    out(f"  [{tag}] labels={[l.strip()[:30] for l in labels[:10]]}")
    out(f"  [{tag}] selects={selects[:6]} buttons={len(buttons)}")


async def dump_frames(page, stamp: str) -> None:
    for i, fr in enumerate(page.frames):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception as exc:
            out(f"  frame {i}: ERR {exc!r}")
            continue
        n_in = html.count("<input")
        out(f"  frame {i}: len={len(html)} inputs={n_in} url={fr.url[:100]}")
        if n_in:
            save_html(f"reg_frame{i}", html, stamp)
            summarize_form(f"frame{i}", html)


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        # 1. portal home → find the login link
        await asyncio.wait_for(
            page.goto(f"{CITIZEN}/Default.aspx", timeout=45000,
                      wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(2500), 10)
        html = await asyncio.wait_for(page.content(), 12)
        save_html("home", html, stamp)

        login_link = None
        for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
            href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2)).lower()
            if "login" in text or "register" in text or "login.aspx" in href.lower():
                login_link = href if href.startswith("http") else f"{CITIZEN}/{href.lstrip('/')}"
                out(f"login-ish link: {text.strip()[:40]!r} → {login_link[:110]}")
        if not login_link:
            login_link = f"{CITIZEN}/Login.aspx"
            out(f"no explicit link found; defaulting to {login_link}")

        # 2. open Login.aspx, dump everything
        await asyncio.wait_for(
            page.goto(login_link, timeout=45000, wait_until="domcontentloaded"),
            55,
        )
        await asyncio.wait_for(page.wait_for_timeout(4000), 10)
        html = await asyncio.wait_for(page.content(), 12)
        save_html("login", html, stamp)
        out(f"login url now: {page.url}")
        out(f"frames: {len(page.frames)}")
        await dump_frames(page, stamp)

        # any visible register-ish control?
        for sel in ["text=Register", "text=Create", "a:has-text('Register')"]:
            try:
                cnt = await asyncio.wait_for(page.locator(sel).count(), 5)
                if cnt:
                    out(f"locator {sel!r}: {cnt} matches")
            except Exception:
                pass

        # 3. click "Register for an Account" if present (popup/new-tab safe)
        try:
            async with asyncio.timeout(25):
                async with page.expect_event("popup", timeout=15000) as pop:
                    await page.locator("text=/register/i").first.click(timeout=8000)
                newp = await pop.value
                out("popup opened → registering new page")
                await asyncio.wait_for(newp.wait_for_load_state("load"), 20)
                await asyncio.wait_for(newp.wait_for_timeout(3000), 8)
                h2 = await asyncio.wait_for(newp.content(), 12)
                save_html("register_popup", h2, stamp)
                out(f"popup url: {newp.url[:130]}")
                out(f"popup frames: {len(newp.frames)}")
                for i, fr in enumerate(newp.frames):
                    fh = await asyncio.wait_for(fr.content(), 10)
                    out(f"  popup frame {i}: len={len(fh)} inputs={fh.count('<input')} url={fr.url[:100]}")
                    if fh.count("<input"):
                        summarize_form(f"popup{i}", fh)
        except Exception as exc:
            out(f"popup path: {exc!r} — checking same-tab navigation instead")
            await asyncio.wait_for(page.wait_for_timeout(2000), 8)
            h3 = await asyncio.wait_for(page.content(), 12)
            if h3 != html:
                save_html("register_sametab", h3, stamp)
                out(f"same-tab url now: {page.url[:130]}, frames={len(page.frames)}")
                await dump_frames(page, stamp)

        await page.screenshot(path=os.path.join(OUTDIR, f"{stamp}_register_recon.png"),
                              full_page=True)
        out("screenshot saved")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
