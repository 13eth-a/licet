"""sweep ni portal caphome ddlgspermittype per module: full type catalog"""
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
TARGETS = [
    "Commercial Alteration",
    "Residential Addition",
    "Commercial Electrical",
    "New Single Family Residence",
    "Solar Permit",
    "Right of Way Use Permit",
    "Sign - Temporary",
]
OUTDIR = os.path.join("logs", "ni_backoffice", "inventory")
TYPE_DD = "ctl00$PlaceHolderMain$generalSearchForm$ddlGSPermitType"


def out(m: str) -> None:
    print(m, flush=True)


def parse_type_dropdown(html: str) -> list[dict[str, str]]:
    m = re.search(
        re.escape(TYPE_DD) + r"\".*?</select>", html, re.S
    )
    if not m:
        return []
    items = []
    for om in re.finditer(r"<option([^>]*)>([^<]*)</option>", m.group(0)):
        attrs, label = om.group(1), om.group(2)
        vm = re.search(r'value="([^"]*)"', attrs)
        label = re.sub(r"\s+", " ", label).strip()
        if label and label != "--Select--":
            items.append({"label": label, "value": vm.group(1) if vm else ""})
    return items


async def main() -> int:
    from solari_browser import Solari

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    catalog: dict[str, list[dict[str, str]]] = {}
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        for module in MODULES:
            url = f"{CITIZEN}/Cap/CapHome.aspx?TabName=Home&module={module}"
            try:
                await asyncio.wait_for(
                    page.goto(url, timeout=35000, wait_until="domcontentloaded"), 45
                )
                await asyncio.wait_for(page.wait_for_timeout(1500), 8)
                html = await asyncio.wait_for(page.content(), 12)
                catalog[module] = parse_type_dropdown(html)
            except Exception as exc:
                out(f"{module}: ERR {exc!r}")
                catalog[module] = []
            out(f"{module}: {len(catalog[module])} configured types")
            with open(os.path.join(OUTDIR, f"{stamp}_catalog.json"), "w") as f:
                json.dump(catalog, f, indent=2)

        out("\n=== target match (exact label) ===")
        for target in TARGETS:
            hits = [
                (mod, t["value"])
                for mod, items in catalog.items()
                for t in items
                if t["label"].lower() == target.lower()
            ]
            if hits:
                for mod, val in hits:
                    out(f"  ✅ {target}  [{mod}]  value={val!r}")
            else:
                close = [
                    (mod, t["label"])
                    for mod, items in catalog.items()
                    for t in items
                    if all(w in t["label"].lower() for w in target.lower().split()[:2])
                ]
                out(f"  ❌ {target} — close: {close[:4]}")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
