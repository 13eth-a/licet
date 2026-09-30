"""read only: dump the full scheduling wizard inspection type grid for records"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from licet.agent.state import AgentState  # noqa: E402
from licet.browser import accela  # noqa: E402
from licet.browser.dispatcher import ToolDispatcher  # noqa: E402
from licet.browser.solari_client import SolariSession  # noqa: E402

OUTDIR = Path("logs/ni_backoffice/offered_types")


def out(message: str) -> None:
    print(message, flush=True)


async def main() -> int:
    argv = sys.argv[1:]

    def value(flag: str, default: str) -> str:
        return argv[argv.index(flag) + 1] if flag in argv else default

    records = [r for r in value("--records", "").split(",") if r.strip()]
    if not records:
        out("no --records given")
        return 2

    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    state = AgentState(goal="Enumerate offered inspection types (read-only)")
    report: dict = {"generated": stamp, "records": {}}
    session = SolariSession()
    try:
        client = await session.client()
        dispatcher = ToolDispatcher(client)
        login = await client.login(
            os.environ.get("ACCELA_TEST_USERNAME", "").strip(),
            os.environ.get("ACCELA_TEST_PASSWORD", "").strip(),
        )
        out(f"login ok={login.ok} authenticated={(login.data or {}).get('authenticated')}")

        for record in records:
            out(f"\n===== {record} =====")
            entry: dict = {}
            await dispatcher.execute(
                {"name": "navigate", "args": {"url": accela.INSPECTION_ENTRY_URL}}, state
            )
            await dispatcher.execute(
                {"name": "wait", "args": {"until_absent": "Loading..."}}, state
            )
            opened = await dispatcher.execute(
                {"name": "click", "args": {"target": record, "by": "text", "intent": "navigate"}},
                state,
            )
            await dispatcher.execute(
                {"name": "wait", "args": {"until_absent": "Loading..."}}, state
            )
            if not opened.get("success"):
                entry["error"] = "record not opened"
                report["records"][record] = entry
                out(f"  could not open {record}")
                continue
            await dispatcher.execute(
                {
                    "name": "click",
                    "args": {
                        "target": "Schedule or Request an Inspection",
                        "by": "text",
                        "intent": "navigate",
                    },
                },
                state,
            )
            await dispatcher.execute(
                {"name": "wait", "args": {"until_present": "Inspection Type"}}, state
            )

            page = await dispatcher.execute(
                {"name": "read_page", "args": {"include": ["text"]}}, state
            )
            text = (page.get("data") or {}).get("text") or ""
            entry["declared_total"] = accela.inspection_type_total(text)

            saved: list[str] = []
            names: list[str] = []
            grid_frame = None
            grid_index: int | None = None
            for index, frame in enumerate(list(getattr(client.page, "frames", []) or [])):
                try:
                    html = await frame.content()
                except Exception:
                    continue
                if "gvInspectionType" not in html and "InspectionType" not in html:
                    continue
                grid_frame, grid_index = frame, index
                path = OUTDIR / f"{stamp}_{record}_typegrid_f{index}.html"
                path.write_text(html, encoding="utf-8")
                saved.append(str(path))
                found = accela.parse_inspection_types(accela.parse_fields(html))
                names = [option.name for option in found]

            # the grid paginates at 10 rows; walk any extra pages so every offered type is enumerated, not
            # just page 1
            if grid_frame is not None:
                for page_no in range(2, 5):
                    try:
                        pager = grid_frame.locator("table.aca_pagination a")
                        if not await pager.count():
                            break
                        await pager.first.click(timeout=8000)
                        await client.page.wait_for_timeout(6000)
                        html = await grid_frame.content()
                    except Exception as exc:
                        entry.setdefault("pager_error", str(exc)[:160])
                        break
                    path = OUTDIR / f"{stamp}_{record}_typegrid_f{grid_index}_p{page_no}.html"
                    path.write_text(html, encoding="utf-8")
                    saved.append(str(path))
                    found = accela.parse_inspection_types(accela.parse_fields(html))
                    added = [o.name for o in found if o.name not in names]
                    names += added
                    out(f"  page {page_no}: {len(found)} types ({len(added)} new)")
                    for name in added:
                        out(f"    + {name}")

            entry["offered"] = names
            entry["offered_page1_count"] = 10
            entry["html"] = saved
            report["records"][record] = entry
            out(f"  declared_total={entry['declared_total']} offered={len(names)} types")
            for name in names:
                out(f"    - {name}")
    except BaseException as exc:  # report, do not mask
        report["error"] = repr(exc)
        out(f"EXCEPTION: {exc!r}")
    finally:
        try:
            await session.close()
        except Exception:
            pass
        path = OUTDIR / f"{stamp}_offered_types.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        out(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
