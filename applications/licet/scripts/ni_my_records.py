"""Read the logged-in citizen portal's My Records grid (ground truth capture).

Verifies what `scripts/ni_apply_batch.py` actually issued: the apply flow's
confirmation page prints "Your Record Number is <ID>", but altID formats vary
per agency config (Sign - Temporary -> BLD26-00467, Commercial Alteration ->
000000014), and a mis-parsed confirmation is indistinguishable from a real
record. My Records is the authoritative list of records owned by the
public-user account, so this is the read-back verification step.

Login -> MyRecordsCap.aspx -> dump HTML/screenshot -> parse the grid rows
(and any capID1/2/3 deep links per row) -> write JSON keyed by record number.

Run:  .venv/bin/python scripts/ni_my_records.py
"""
from __future__ import annotations

import asyncio
import html as _html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SITE = "https://aca-test.accela.com"
CITIZEN = f"{SITE}/nullisland"
OUTDIR = Path("logs/ni_backoffice/inventory")
USER = os.environ.get("ACCELA_TEST_USERNAME", "").strip()
PWD = os.environ.get("ACCELA_TEST_PASSWORD", "").strip()
MY_RECORDS = f"{CITIZEN}/Cap/MyRecordsCap.aspx?TabName=Home"

# records the batch flow claims to have issued (key -> record number)
CLAIMED = {
    "sign_temp": "BLD26-00467",
    "sign_temp_dupe": "BLD26-00466",  # earlier submit run; kept as a spare slot
    "comm_alt": "000000014",
    "res_add": "BLD26-00468",
    "comm_elec": "BLD26-00469",
    "new_sfr": "BLD26-00470",
    "solar": "BLD26-00471",
    "row_use": "BLD26-00472",
}
DETAILS = "--details" in sys.argv
CAPID_RE = re.compile(
    r"capID1=([^&\"']+).*?capID2=([^&\"']+).*?capID3=([^&\"']+)", re.I | re.S)
# record-detail sections worth pinning as eval ground truth
SECTION_WORDS = ("Record Info", "Processing Status", "Inspection", "Fees",
                 "Attachments", "Related Records", "Payment", "Conditions",
                 "Parcels", "Contacts", "Licensed Professional", "Reviews",
                 "Documents", "Work Location", "Detail Information")
ROW_SPLIT_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)

# ACA renders the grid's pager as an ordinary <tr> inside the same table, so a
# header-driven read counts it as a record — its "Record Number" cell holds the
# page number. Observed live 2026-09-30: a page declaring "showing 1-10 of 14"
# parsed as 11 rows (the 11th being `Date='< Prev'`, `Record Number='1'`,
# `Project Name='Next >'`), which failed the declared-range check and aborted
# the whole capacity query with `record_grid_rows_do_not_match_declared_range`.
# The pager is not a record, so it is not returned as one.
PAGER_CELL_RE = re.compile(
    r"^\s*(?:&lt;|&gt;|<|>)*\s*(?:prev(?:ious)?|next|more)\s*"
    r"(?:&lt;|&gt;|<|>)*\s*$",
    re.I,
)


def out(m: str) -> None:
    print(m, flush=True)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def clean(fragment: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", txt).strip()


async def dump_page(page, stamp: str, name: str) -> None:
    try:
        await asyncio.wait_for(
            page.screenshot(path=str(OUTDIR / f"{stamp}_{name}.png")), 15)
    except Exception as exc:
        out(f"  shot fail: {exc!r}")
    for i, fr in enumerate(list(page.frames)):
        try:
            html = await asyncio.wait_for(fr.content(), 10)
        except Exception:
            continue
        (OUTDIR / f"{stamp}_{name}_f{i}.html").write_text(html, encoding="utf-8")


async def login(page) -> None:
    await asyncio.wait_for(
        page.goto(f"{CITIZEN}/Login.aspx", timeout=45000,
                  wait_until="domcontentloaded"), 55)
    await asyncio.wait_for(page.wait_for_timeout(3500), 10)
    fr = None
    for f in page.frames:
        if "login-panel" in f.url:
            fr = f
            break
    if fr is None:
        raise RuntimeError("login panel not found")
    for sel, val in (("input[name='username']", USER),
                     ("input[name='password']", PWD)):
        await asyncio.wait_for(fr.locator(sel).first.fill(val, timeout=8000), 10)
    try:
        await asyncio.wait_for(fr.locator("button").first.click(timeout=6000), 8)
    except Exception:
        await fr.locator("input[name='password']").first.press("Enter")
    await asyncio.wait_for(page.wait_for_load_state("load"), 25)
    await asyncio.wait_for(page.wait_for_timeout(4000), 10)
    out(f"post-login: {page.url[:100]}")


async def body_text(page) -> str:
    txt = ""
    for f in list(page.frames):
        try:
            txt += await asyncio.wait_for(f.locator("body").inner_text(), 8)
        except Exception:
            continue
    return txt


def parse_grid(html: str) -> list[dict]:
    """Rows of the data grid whose header includes 'Record Number'.

    Header-driven, so we get named fields (Date / Record Number / Record Type
    / Address / Status / Description / Expiration Date) instead of raw cells.
    """
    headers: list[str] = []
    rows: list[dict] = []
    for m in ROW_SPLIT_RE.finditer(html):
        row = m.group(1)
        # keep EMPTY cells: dropping them shifts every column left (the grid
        # has an empty Project Name column on these rows)
        cells = [clean(c) for c in CELL_RE.findall(row)]
        if not any(cells):
            continue
        if any(PAGER_CELL_RE.match(c) for c in cells):
            continue
        if not headers:
            if "Record Number" in cells:
                headers = cells
            continue
        if len(cells) < 2:
            continue
        # a row whose first cell is a header word means a second grid started
        if cells[0] in ("Date", "Record Number"):
            continue
        rec = {headers[i] if i < len(headers) else f"col{i}": v
               for i, v in enumerate(cells[:len(headers)])}
        capids = None
        cm = CAPID_RE.search(html[m.start():m.end()])
        if cm:
            capids = {"capID1": cm.group(1), "capID2": cm.group(2),
                      "capID3": cm.group(3)}
        hrefs = [h for h in re.findall(r'href="([^"]+)"', row)
                 if "javascript" not in h]
        rec["capids"] = capids
        rec["hrefs"] = hrefs[:4]
        rows.append(rec)
    return rows


async def capture_detail(page, rid: str, href: str) -> dict:
    """Read one record's detail page: status, type, and section links."""
    # grid hrefs are site-absolute (/NULLISLAND/Cap/...), not agency-relative
    url = f"{SITE}{_html.unescape(href)}"
    info: dict = {"url": url}
    try:
        await asyncio.wait_for(
            page.goto(url, timeout=45000, wait_until="domcontentloaded"), 55)
        await asyncio.wait_for(page.wait_for_timeout(3000), 10)
        body = await body_text(page)
        for _ in range(8):
            if len(body.strip()) > 300:
                break
            await asyncio.wait_for(page.wait_for_timeout(1200), 5)
            body = await body_text(page)
        info["body_head"] = body[:800]
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        info["lines"] = lines[:60]
        links: list[str] = []
        for f in list(page.frames):
            try:
                texts = await asyncio.wait_for(
                    f.locator("a:visible").all_text_contents(), 10)
            except Exception:
                continue
            for t in texts:
                t = re.sub(r"\s+", " ", t).strip()
                if t and any(w.lower() in t.lower() for w in SECTION_WORDS):
                    if t not in links:
                        links.append(t)
        info["section_links"] = links[:30]
        info["title"] = (await page.title())[:120]
    except Exception as exc:
        info["error"] = repr(exc)
    return info


async def main() -> int:
    from solari_browser import Solari

    if not USER or not PWD:
        out("credentials missing")
        return 2
    solari = Solari(api_key=os.environ["SOLARI_API_KEY"].strip())
    browser = await asyncio.wait_for(solari.launch(), 120)
    stamp = stamp_now()
    data: dict = {"generated": stamp, "my_records_url": MY_RECORDS,
                  "records": {}, "missing": [], "extra_rows": []}
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)
        await login(page)
        await asyncio.wait_for(
            page.goto(MY_RECORDS, timeout=45000,
                      wait_until="domcontentloaded"), 55)
        await asyncio.wait_for(page.wait_for_timeout(4500), 10)
        body = await body_text(page)
        for _ in range(10):
            if len(body.strip()) > 400:
                break
            await asyncio.wait_for(page.wait_for_timeout(1200), 5)
            body = await body_text(page)
        await dump_page(page, stamp, "my_records")
        data["body_excerpt"] = body[:1500]

        # the grid may live in the top frame or an inner frame; parse all
        rows: list[dict] = []
        for f in list(page.frames):
            try:
                html = await asyncio.wait_for(f.content(), 10)
            except Exception:
                continue
            rows.extend(parse_grid(html))
        data["grid_rows"] = rows

        claimed_ids = set(CLAIMED.values())
        found = {r.get("Record Number", "") for r in rows}
        for key, rid in CLAIMED.items():
            if rid not in found:
                data["missing"].append({"key": key, "claimed": rid})
        for r in rows:
            rid = r.get("Record Number", "")
            if rid:
                data["records"][rid] = {
                    "key": next((k for k, v in CLAIMED.items() if v == rid), ""),
                    **r}
            elif r:
                data["extra_rows"].append(r)

        out(f"\n=== My Records: {len(found & claimed_ids)}/{len(CLAIMED)} "
            f"claimed IDs in grid; {len(rows)} rows total ===")
        for r in rows:
            out(f"  {r.get('Date', '')} | {r.get('Record Number', '')} | "
                f"{r.get('Record Type', '')} | {r.get('Address', '')} | "
                f"{r.get('Status', '')} | {r.get('Description', '')}")
        if data["missing"]:
            out("\nnot found by claimed id (altID format may differ):")
            for m in data["missing"]:
                out(f"  {m['key']} claimed={m['claimed']}")

        if DETAILS:
            out("\n=== record detail read-back ===")
            for rid, rec in data["records"].items():
                href = next((h for h in rec.get("hrefs", [])
                             if "CapDetail" in h), "")
                if not href:
                    out(f"  {rid}: no detail link")
                    continue
                det = await capture_detail(page, rid, href)
                rec["detail"] = det
                if det.get("error"):
                    out(f"  {rid}: ERROR {det['error']}")
                    continue
                out(f"  {rid} | sections: "
                    f"{', '.join(det['section_links'][:12]) or 'none'}")
            await dump_page(page, stamp, "record_detail_last")

        (OUTDIR / f"{stamp}_my_records.json").write_text(
            json.dumps(data, indent=2))
        return 0
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
