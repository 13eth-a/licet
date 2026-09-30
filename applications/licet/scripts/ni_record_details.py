"""Open every Null Island record's capDetail.do and capture full details.

capDetail.do?mode=view&serviceProviderCode=NULLISLAND&ID1=&ID2=&ID3=&module=
renders the Record section with readonly inputs:
    value(capType)     → e.g. "Building/Residential/Mechanical/NA"
    value(capStatus)   → record status (also plain text)
plus Opened Date, Application Name, Description, fee totals.

READ-ONLY. Checkpoints JSON after each record.

Run:  .venv/bin/python scripts/ni_record_details.py
"""

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
SHOTS = os.path.join("logs", "ni_backoffice", "inventory")
INVENTORY = sorted(
    (os.path.join(SHOTS, f) for f in os.listdir(SHOTS) if f.endswith("_inventory.json")),
    key=os.path.getmtime,
)[-1]
DETAIL_TIMEOUT = 40


def out(msg: str) -> None:
    print(msg, flush=True)


def input_value(html: str, name: str) -> str:
    m = re.search(
        rf'<input[^>]*name="{re.escape(name)}"[^>]*value="([^"]*)"', html
    )
    if not m:
        m = re.search(
            rf'<input[^>]*value="([^"]*)"[^>]*name="{re.escape(name)}"', html
        )
    return m.group(1).strip() if m else ""


async def main() -> int:
    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty.")
        return 2

    with open(INVENTORY) as f:
        records: dict[str, dict] = json.load(f)
    out(f"Inventory: {INVENTORY} ({len(records)} records)")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(SHOTS, f"{stamp}_details.json")

    from solari_browser import Solari

    out("Launching Solari cloud browser…")
    solari = Solari(api_key=api_key)
    browser = await asyncio.wait_for(solari.launch(), 120)
    done = 0
    try:
        page = await asyncio.wait_for(browser.new_page(), 60)

        # --- login ---
        await asyncio.wait_for(
            page.goto(AV_URL, timeout=60000, wait_until="domcontentloaded"), 70
        )
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

        for cap_id, rec in records.items():
            c1, c2, c3 = cap_id.split("-")
            module = rec.get("module") or "Building"
            url = (
                f"{AV_URL}portlets/cap/capDetail.do?mode=view"
                f"&serviceProviderCode=NULLISLAND&ID1={c1}&ID2={c2}&ID3={c3}"
                f"&isRedirect=false&module={module}"
            )
            try:
                await asyncio.wait_for(
                    page.goto(url, timeout=DETAIL_TIMEOUT * 1000,
                              wait_until="domcontentloaded"),
                    DETAIL_TIMEOUT + 10,
                )
                await asyncio.wait_for(page.wait_for_timeout(2000), 10)

                # capDetail renders in the main frame or a child — take the
                # largest frame containing value(capType)
                best_html = ""
                for fr in page.frames:
                    try:
                        h = await asyncio.wait_for(fr.content(), 15)
                    except Exception:
                        continue
                    if "value(capType)" in h and len(h) > len(best_html):
                        best_html = h
                if not best_html:
                    rec["detail_ok"] = False
                    rec["detail_error"] = "capType input not found"
                    out(f"{cap_id}: no capType input found")
                else:
                    rec["cap_type"] = input_value(best_html, "value(capType)")
                    rec["record_status"] = input_value(best_html, "value(capStatus)")
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
                    out(
                        f"{cap_id}: {rec['cap_type']!r} "
                        f"status={rec['record_status']!r}"
                    )
            except Exception as exc:
                rec["detail_ok"] = False
                rec["detail_error"] = repr(exc)[:200]
                out(f"{cap_id}: FAILED — {exc!r}")

            with open(out_path, "w") as f:
                json.dump(records, f, indent=2)
    finally:
        try:
            await asyncio.wait_for(browser.close(), 30)
        except Exception:
            pass

    # --- summarize by full type ---
    out(f"\n=== details for {done}/{len(records)} records ===")
    by_type: dict[str, list[dict]] = {}
    for r in records.values():
        by_type.setdefault(r.get("cap_type") or "(none)", []).append(r)
    for t in sorted(by_type):
        rs = by_type[t]
        out(f"  {t}: {len(rs)}")
        for r in rs[:6]:
            out(
                f"      {r.get('alt_id', '?'):>14}  {r.get('record_status', '')}"
                f"  {r.get('app_name', '')[:60]}"
            )
        if len(rs) > 6:
            out(f"      … +{len(rs) - 6} more")
    out(f"\nJSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
