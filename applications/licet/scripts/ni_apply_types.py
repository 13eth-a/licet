"""Enumerate record types offered by Null Island's APPLY flow (read-only).

The back-office inventory (ni_record_inventory.py) shows which types HAVE
records. The apply flow (citizen portal CapWiz) shows which types are
CONFIGURED for application — types can exist with 0 records. This script
GETs the CapWiz type-chooser for each module and dumps every <select>'s
options so we can match Licet's 7 target categories against what a citizen
can actually apply for.

Run:  .venv/bin/python scripts/ni_apply_types.py
Read-only: no application is started or submitted; type-chooser pages only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

CITIZEN = "https://aca-test.accela.com/nullisland"
MODULES = [
    "Building", "Planning", "PublicWorks", "Fire", "Enforcement",
    "ServiceRequest", "EnvHealth", "AMS", "Cannabis", "Licenses",
    "Treasury", "MyPortal",
]
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_selects(html: str) -> dict[str, list[str]]:
    """Extract every <select> with its non-empty option labels."""
    selects: dict[str, list[str]] = {}
    for m in re.finditer(r"<select[^>]*>", html):
        tag = m.group(0)
        name_m = re.search(r'name="([^"]+)"', tag) or re.search(r'id="([^"]+)"', tag)
        name = name_m.group(1) if name_m else f"select_{m.start()}"
        end = html.find("</select>", m.end())
        if end == -1:
            continue
        body = html[m.end():end]
        labels = []
        for om in re.finditer(r"<option[^>]*>([^<]*)</option>", body):
            label = re.sub(r"\s+", " ", om.group(1)).strip()
            if label and label not in labels:
                labels.append(label)
        if labels:
            selects[name] = labels
    return selects


def save(data: dict, stamp: str) -> str:
    path = os.path.join(OUTDIR, f"{stamp}_apply_types.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


async def main() -> int:
    from solari_browser import Solari

    api_key = os.environ["SOLARI_API_KEY"].strip()
    solari = Solari(api_key=api_key)
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    data: dict = {"generated": stamp, "modules": {}}
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        for module in MODULES:
            url = f"{CITIZEN}/Cap/CapWiz.do?module={module}"
            entry = {"url": url}
            try:
                await asyncio.wait_for(
                    page.goto(url, timeout=45000, wait_until="domcontentloaded"), 55
                )
                await asyncio.wait_for(page.wait_for_timeout(2500), 10)
            except Exception as exc:
                entry["error"] = repr(exc)
                data["modules"][module] = entry
                save(data, stamp)
                continue

            final_url = page.url
            entry["final_url"] = final_url
            try:
                html = await asyncio.wait_for(page.content(), 15)
            except Exception as exc:
                entry["error"] = f"content: {exc!r}"
                data["modules"][module] = entry
                save(data, stamp)
                continue

            low = html.lower()
            if "access is denied" in low or "capsearchform" in low and "denied" in low:
                entry["gated"] = "access-denied"
            elif "login.aspx" in final_url.lower() or "sign in" in low[:3000]:
                entry["gated"] = "redirected-to-login"
            else:
                selects = parse_selects(html)
                entry["selects"] = selects
                # compact type list for eyeballing
                types: set[str] = set()
                for labels in selects.values():
                    for lab in labels:
                        if re.search(r"/|permit|sign|solar|row|right of way|addition|alteration|new|residential|commercial|electrical|mechanical", lab, re.I):
                            types.add(lab)
                entry["type_like_options"] = sorted(types)
            data["modules"][module] = entry
            n_sel = len(entry.get("selects", {}))
            out(f"{module}: gated={entry.get('gated', 'no')} selects={n_sel} "
                f"type_opts={len(entry.get('type_like_options', []))}")
            if module == MODULES[0]:
                await page.screenshot(
                    path=os.path.join(OUTDIR, f"{stamp}_apply_{module}.png")
                )
            save(data, stamp)  # checkpoint after each module

        # summary
        out("\n=== summary ===")
        for module, entry in data["modules"].items():
            if "selects" in entry:
                interesting = entry.get("type_like_options", [])
                out(f"{module}: {len(interesting)} type-like options")
                for t in interesting[:12]:
                    out(f"    - {t}")
            else:
                out(f"{module}: {entry.get('gated') or entry.get('error')}")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
