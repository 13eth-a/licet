"""full read only inventory of null island records (all modules)"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
MODULES = [
    "Building", "Planning", "PublicWorks", "Fire", "Enforcement",
    "EnvHealth", "ServiceRequest", "Licenses", "Cannabis", "RentalHousing",
    "AMS", "Administration",
]
SHOTS = os.path.join("logs", "ni_backoffice", "inventory")
MAX_PAGES_PER_MODULE = 30
GOTO_TIMEOUT = 45
CONTENT_TIMEOUT = 15

ID_RE = re.compile(r'value="([A-Z]{2,10}\d{2}-\d{5})"')
TYPE_HINT_RE = re.compile(
    r"(Commercial|Residential|Solar|Sign|Right of Way|ROW|Electrical|Mechanical|"
    r"Plumbing|New|Addition|Alteration|Fence|Wall|Water Heater|Furnace|Air "
    r"Condition|Footing|Foundation|Roof|Demolition|Temp|Site Plan|Subdivision|"
    r"Variance|Use)",
    re.I,
)


def out(msg: str) -> None:
    print(msg, flush=True)


async def goto_bounded(page, url: str) -> None:
    await asyncio.wait_for(
        page.goto(url, timeout=GOTO_TIMEOUT * 1000, wait_until="domcontentloaded"),
        GOTO_TIMEOUT + 10,
    )


def parse_rows(html: str, module: str) -> list[dict]:
    """extract grid rows: alt id, capid triple, status, record type column"""
    rows: list[dict] = []
    for tr in re.split(r"<tr[\s>]", html):
        c1 = re.search(r'name="value\(CAPID1,\d+\)"[^>]*value="([^"]*)"', tr)
        c2 = re.search(r'name="value\(CAPID2,\d+\)"[^>]*value="([^"]*)"', tr)
        c3 = re.search(r'name="value\(CAPID3,\d+\)"[^>]*value="([^"]*)"', tr)
        if not (c1 and c2 and c3):
            continue
        alt = re.search(r'name="value\(altID,\d+\)"[^>]*value="([^"]*)"', tr)
        mod = re.search(r'name="value\(moduleName,\d+\)"[^>]*value="([^"]*)"', tr)
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        ]
        cells = [c for c in cells if c and c != "&nbsp;"]
        type_cell = next((c for c in cells if TYPE_HINT_RE.search(c)), "")
        rows.append(
            {
                "alt_id": alt.group(1) if alt else "",
                "cap_id": f"{c1.group(1)}-{c2.group(1)}-{c3.group(1)}",
                "module": mod.group(1) if mod else module,
                "grid_type": type_cell[:40],
                "grid_status": cells[1] if len(cells) > 1 else "",
                "grid_cells": cells[:12],
            }
        )
    return rows


def parse_total_pages(html: str) -> int:
    m = re.search(r"totalPages=(\d+)", html)
    return int(m.group(1)) if m else 1


async def grid_html(page) -> str:
    """largest frame that looks like a record grid (main doc counts too)"""
    candidates: list[str] = []
    for fr in page.frames:
        try:
            h = await asyncio.wait_for(fr.content(), CONTENT_TIMEOUT)
        except Exception:
            continue
        if "capSearch" in h or 'name="value(CAPID1,' in h:
            candidates.append(h)
    return max(candidates, key=len) if candidates else ""


def input_value(html: str, name: str) -> str:
    m = re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(rf'<input[^>]*value="([^"]*)"[^>]*name="{re.escape(name)}"', html)
    return m.group(1).strip() if m else ""


def save(records: dict[str, dict], stamp: str, suffix: str) -> str:
    path = os.path.join(SHOTS, f"{stamp}_{suffix}.json")
    with open(path, "w") as f:
        json.dump(records, f, indent=2)
    return path


async def main() -> int:
    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty.")
        return 2
    os.makedirs(SHOTS, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    from solari_browser import Solari

    out("Launching Solari cloud browser…")
    solari = Solari(api_key=api_key)
    browser = await asyncio.wait_for(solari.launch(), 120)
    records: dict[str, dict] = {}
    sweep_path = ""
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        await goto_bounded(page, AV_URL)
        await asyncio.wait_for(page.wait_for_timeout(5000), 15)
        user = page.locator('input[name="username"]').first
        if not await user.is_visible():
            user = page.locator('input[type="text"]').first
        await asyncio.wait_for(user.fill(USER), 15)
        await asyncio.wait_for(
            page.locator('input[type="password"]').first.fill(PASSWORD), 15
        )
        try:
            await asyncio.wait_for(
                page.locator('input[type="submit"]').first.click(timeout=5000), 15
            )
        except Exception:
            await asyncio.wait_for(page.keyboard.press("Enter"), 15)
        await asyncio.wait_for(page.wait_for_timeout(10000), 20)
        out("Logged in.")

        for module in MODULES:
            page_no = 1
            total_pages = 1
            module_rows = 0
            while page_no <= total_pages and page_no <= MAX_PAGES_PER_MODULE:
                url = (
                    f"{AV_URL}portlets/cap/capSearch.do?pageNo={page_no}"
                    f"&totalPages={total_pages}&column=altID&module={module}"
                    f"&spaceName=spaces.nullisland.record&isGeneralCAP=Y"
                )
                try:
                    await goto_bounded(page, url)
                except Exception as exc:
                    out(f"{module} p{page_no}: goto failed — {exc!r}")
                    break
                await asyncio.wait_for(page.wait_for_timeout(1500), 10)
                html = await grid_html(page)
                rows = parse_rows(html, module)
                total_pages = parse_total_pages(html)
                for r in rows:
                    records.setdefault(r["cap_id"], r)
                module_rows += len(rows)
                if rows or page_no == 1:
                    out(f"{module} p{page_no}/{total_pages}: {len(rows)} rows")
                page_no += 1
            out(f"{module}: {module_rows} rows this module")
            sweep_path = save(records, stamp, "sweep")
        out(f"\nPhase 1 done: {len(records)} unique records → {sweep_path}")

        done = 0
        for cap_id, rec in records.items():
            c1, c2, c3 = cap_id.split("-")
            module = rec.get("module") or "Building"
            url = (
                f"{AV_URL}portlets/cap/capDetail.do?mode=view"
                f"&serviceProviderCode=NULLISLAND&ID1={c1}&ID2={c2}&ID3={c3}"
                f"&isRedirect=false&module={module}"
            )
            try:
                await goto_bounded(page, url)
                await asyncio.wait_for(page.wait_for_timeout(1800), 8)
                best_html = ""
                for fr in page.frames:
                    try:
                        h = await asyncio.wait_for(fr.content(), CONTENT_TIMEOUT)
                    except Exception:
                        continue
                    if "value(capType)" in h and len(h) > len(best_html):
                        best_html = h
                if best_html:
                    rec["cap_type"] = input_value(best_html, "value(capType)")
                    rec["record_status"] = input_value(
                        best_html, "value(capStatus)"
                    )
                    rec["app_name"] = input_value(
                        best_html, "value(capDetailModel.appName)"
                    )[:120]
                    rec["description"] = input_value(
                        best_html, "value(capDetailModel.description)"
                    )[:200]
                    rec["opened_date"] = input_value(
                        best_html, "value(capDetailModel.fileDate)"
                    )
                    rec["detail_ok"] = True
                    done += 1
                    out(f"{cap_id}: {rec['cap_type']!r}")
                else:
                    rec["detail_ok"] = False
                    out(f"{cap_id}: capType input not found")
            except Exception as exc:
                rec["detail_ok"] = False
                rec["detail_error"] = repr(exc)[:160]
                out(f"{cap_id}: FAILED — {exc!r}")
            save(records, stamp, "full")
        out(f"\nPhase 2 done: details for {done}/{len(records)}")
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass

    out(f"\n=== {len(records)} unique records across {len(MODULES)} modules ===")
    by_type: dict[str, list[dict]] = {}
    for r in records.values():
        by_type.setdefault(r.get("cap_type") or "(no detail)", []).append(r)
    for t in sorted(by_type):
        rs = by_type[t]
        out(f"  {t}: {len(rs)}")
        for r in rs[:6]:
            out(
                f"      {r.get('alt_id') or r.get('cap_id', '?'):>15}  "
                f"{r.get('record_status', '') or r.get('grid_status', ''):>18}  "
                f"{(r.get('app_name') or r.get('grid_type') or '')[:55]}"
            )
        if len(rs) > 6:
            out(f"      … +{len(rs) - 6} more")

    out("\n=== match vs Licet target categories ===")
    targets = {
        "Commercial Alteration": r"commercial.*alteration",
        "Residential Addition": r"residential.*addition",
        "Commercial Electrical": r"commercial.*electrical",
        "New Single Family Residence": r"(residential.*new|new.*residence|single family)",
        "Solar Permit": r"solar",
        "Right of Way Use Permit": r"(right of way|row)",
        "Sign - Temporary": r"(sign.*temp|temp.*sign)",
    }
    found_any = False
    for label, pattern in targets.items():
        hits = [
            r
            for r in records.values()
            if re.search(pattern, (r.get("cap_type") or ""), re.I)
            or re.search(pattern, (r.get("grid_type") or ""), re.I)
        ]
        if hits:
            found_any = True
            ids = [r.get("alt_id") or r["cap_id"] for r in hits]
            out(f"  ✔ {label}: {len(hits)} → {', '.join(ids[:8])}")
        else:
            out(f"  ✘ {label}: none")
    if not found_any:
        out(
            "\nNo existing records match the target categories — the sandbox"
            "\ninventory is Mechanical/Site-Plan heavy. New records must be"
            "\ncreated via 'New → Create By Form' in the back office (write op,"
            "\nneeds explicit approval) or the categories mapped to closest"
            "\navailable types."
        )
    out(f"\nJSON: {save(records, stamp, 'full')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
