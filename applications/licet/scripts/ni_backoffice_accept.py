"""Drive one back-office task to acceptance (WARNING: --apply writes).

This is the write half of the Phase 4 unblock. `--inspect` is read-only and
prints the task form's own options so the write can be reviewed first; `--apply`
then sets the disposition + status date and presses the task's `Submit`.

Controls (from the captured task HTML, 2026-09-22):
    value(taskItem*disposition)          Status            <select> (required)
    date(taskItem*statusDate)            Status Date       <input>  (required)
    value(department)                    Department        <select> (required)
    value(actionUser*userID)             Staff             <select> (required)
    value(taskItem*dispositionComment)   Note              <textarea>
    Submit                               posts the task with button name 'Submit'

Nothing outside those controls is touched; assignments, time entries and other
task buttons are ignored.

Run:
    .venv/bin/python scripts/ni_backoffice_accept.py --record BLD26-00469 --inspect
    .venv/bin/python scripts/ni_backoffice_accept.py --record BLD26-00469 \
        --apply --disposition "Accepted - Plan Review Not Req"
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
SHOTS = os.path.join("logs", "ni_backoffice")
DISPOSITION_SELECT = "value(taskItem*disposition)"
STATUS_DATE_INPUT = "date(taskItem*statusDate)"
DEPARTMENT_SELECT = "value(department)"
STAFF_SELECT = "value(actionUser*userID)"
NOTE_TEXTAREA = "value(taskItem*dispositionComment)"


def out(message: str) -> None:
    print(message, flush=True)


async def find_first_visible(scope, selectors: list[str]):
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def dom_click(page, text: str) -> bool:
    """Click the innermost navigation element containing `text` (never a button)."""
    script = """(needle) => {
        const all = [...document.querySelectorAll('*')].filter(e => {
            const tag = (e.tagName || '').toUpperCase();
            if (['BUTTON','INPUT','SELECT','SCRIPT','STYLE','HTML','BODY'].includes(tag)) return false;
            return ((e.innerText || '').trim()).includes(needle);
        });
        if (!all.length) return null;
        all.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
        let el = all[0];
        const anchor = el.querySelector('a');
        if (anchor) el = anchor;
        const label = (el.innerText || '').trim().slice(0, 60);
        el.click();
        return el.tagName + '|' + label;
    }"""
    for index, scope in enumerate((page, *page.frames)):
        try:
            result = await scope.evaluate(script, text)
        except Exception:
            continue
        if result:
            out(f"    clicked {result} in scope {index}")
            return True
    return False


async def click_task_row(page, record: str) -> str | None:
    """From the dashboard task queue, click the task *title* link in `record`'s row.

    Clicking the record-id anchor navigates to the record; the task title anchor is
    what opens the task detail form.
    """
    script = """(id) => {
        const rows = [...document.querySelectorAll('tr,li,div,td')].filter(r =>
            ((r.innerText || '').includes(id)) && r.querySelector('a'));
        if (!rows.length) return null;
        rows.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
        for (const row of rows) {
            const anchors = [...row.querySelectorAll('a')].filter(a => {
                const t = (a.innerText || '').trim();
                return t && !t.includes(id);
            });
            if (!anchors.length) continue;
            const title = (anchors[0].innerText || '').trim();
            anchors[0].click();
            return title;
        }
        return null;
    }"""
    for index, scope in enumerate((page, *page.frames)):
        try:
            result = await scope.evaluate(script, record)
        except Exception:
            continue
        if result:
            out(f"    clicked task {result!r} in scope {index}")
            return result
    return None


async def click_task_link(page) -> str | None:
    """Inside the record's Workflow Tasks portlet, click the active task link."""
    script = """() => {
        const wanted = /application|acceptance|submittal|issue|review|inspection/i;
        const anchors = [...document.querySelectorAll('a')].filter(a => {
            const t = (a.innerText || '').trim();
            return t && wanted.test(t) && t.length < 80;
        });
        if (!anchors.length) return null;
        const label = (anchors[0].innerText || '').trim();
        anchors[0].click();
        return label;
    }"""
    for index, scope in enumerate((page, *page.frames)):
        try:
            if index and "workflowtasklist" not in (scope.url or "").lower():
                continue
            result = await scope.evaluate(script)
        except Exception:
            continue
        if result:
            out(f"    clicked task {result!r} in scope {index}")
            return result
    return None


async def form_frame(page):
    """The frame holding the task form."""
    for scope in (page, *page.frames):
        try:
            found = await scope.evaluate(
                "(name) => !!document.querySelector(`[name=\"${name}\"]`)", DISPOSITION_SELECT
            )
        except Exception:
            continue
        if found:
            return scope
    return None


async def read_form(scope) -> dict:
    return await scope.evaluate(
        """(names) => {
            const get = n => document.querySelector(`[name="${n}"]`);
            const options = el => el ? [...el.options].map(o => o.value) : null;
            const dispo = get(names.dispo), dept = get(names.dept), staff = get(names.staff);
            const frameText = (document.body.innerText || '');
            const titleMatch = frameText.match(/Task Details[\s\S]{0,120}/);
            const dateEl = get(names.date), note = get(names.note);
            const submitOk = [...document.querySelectorAll('a,input,button')].some(e =>
                ((e.getAttribute('onclick') || '').includes("'Submit'")) ||
                ((e.innerText || '').trim() === 'Submit'));
            return {
                disposition_options: options(dispo),
                disposition_value: dispo ? dispo.value : null,
                department_options: options(dept),
                department_value: dept ? dept.value : null,
                staff_options: options(staff),
                staff_value: staff ? staff.value : null,
                status_date: dateEl ? dateEl.value : null,
                note_present: !!note,
                submit_present: submitOk,
                task_context: titleMatch ? titleMatch[0].replace(/\s+/g, ' ').slice(0, 160) : frameText.replace(/\s+/g, ' ').slice(0, 160),
            };
        }""",
        {"dispo": DISPOSITION_SELECT, "dept": DEPARTMENT_SELECT, "staff": STAFF_SELECT,
         "date": STATUS_DATE_INPUT, "note": NOTE_TEXTAREA},
    )


async def set_field(scope, name: str, value: str) -> str | None:
    return await scope.evaluate(
        """([name, value]) => {
            const el = document.querySelector(`[name="${name}"]`);
            if (!el) return null;
            el.value = value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('blur', {bubbles: true}));
            return el.value;
        }""",
        [name, value],
    )


async def submit_controls(page, *, exclude=None) -> list[tuple[int, str, str]]:
    """Every submit-like control visible across the page, with its frame index."""
    script = """() => {
        const els = [...document.querySelectorAll('a,input,button')];
        return els.filter(e => {
            const onclick = e.getAttribute('onclick') || '';
            const text = (e.innerText || e.value || '').trim();
            if (!/'Submit'/.test(onclick) && !/^submit$/i.test(text)) return false;
            const style = window.getComputedStyle(e);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            return true;
        }).map(e => e.tagName + '|' + ((e.innerText || e.value || '').trim().slice(0, 24)) + '|' +
                   (e.getAttribute('onclick') || '').slice(0, 90));
    }"""
    found: list[tuple[int, str, str]] = []
    for index, scope in enumerate((page, *page.frames)):
        if exclude is not None and index == exclude:
            continue
        try:
            for item in await scope.evaluate(script):
                tag, text, onclick = item.split("|", 2)
                found.append((index, f"{tag}|{text}", onclick))
        except Exception:
            continue
    return found


async def press_submit(page, frame_index: int | None) -> str | None:
    """Click the task's Submit control. Searches every frame, form frame first.

    The workflow task menu renders SUBMIT as a bare element (not an anchor), so the
    fallback walks every node whose text is exactly 'SUBMIT' and clicks it (or its
    nearest anchor ancestor).
    """
    script = """() => {
        const label = el => el.tagName + '|' + (el.innerText || el.value || '').trim().slice(0, 24);
        const els = [...document.querySelectorAll('a,input,button')];
        let el = els.find(e => (e.getAttribute('onclick') || '').includes("'Submit'"));
        if (!el) {
            const menu = [...document.querySelectorAll('*')].filter(e =>
                ((e.innerText || '').trim()).toLowerCase() === 'submit');
            menu.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
            if (menu.length) {
                const leaf = menu[0];
                el = leaf.closest('a') || leaf.querySelector('a') || leaf;
            }
        }
        if (!el) return null;
        const text = label(el);
        el.click();
        return text;
    }"""
    order = list(range(len(page.frames)))
    if frame_index is not None and frame_index in order:
        order.remove(frame_index)
        order.insert(0, frame_index)
    scopes = [page, *page.frames]
    for index in order:
        try:
            result = await scopes[index].evaluate(script)
        except Exception:
            continue
        if result:
            out(f"    submit control {result!r} clicked in frame {index}")
            return result
    return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default="BLD26-00469")
    parser.add_argument("--inspect", action="store_true", help="read the task form only (default)")
    parser.add_argument("--apply", action="store_true", help="set fields and press Submit")
    parser.add_argument("--disposition", default="Accepted - Plan Review Not Req")
    parser.add_argument("--status-date", default=None, help="MM/DD/YYYY (default: today)")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if not args.apply:
        args.inspect = True
    status_date = args.status_date or date.today().strftime("%m/%d/%Y")

    api_key = os.environ.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        out("SOLARI_API_KEY is empty.")
        return 2

    os.makedirs(SHOTS, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    from solari_browser import Solari

    solari = Solari(api_key=api_key)
    out(f"Launching Solari cloud browser… (mode={'apply' if args.apply else 'inspect'})")
    browser = await solari.launch()
    try:
        page = await browser.new_page()
        await page.goto(AV_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        user = await find_first_visible(page, ['input[name="username"]', 'input[id*="user" i]', 'input[type="text"]'])
        password = await find_first_visible(page, ['input[type="password"]'])
        if user is None or password is None:
            out("login inputs not found — aborting")
            return 1
        await user.fill(USER)
        await password.fill(PASSWORD)
        submit = await find_first_visible(page, ['input[type="submit"]', "button[type=submit]"])
        if submit is not None:
            await submit.click()
        else:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(9000)
        out("logged in")

        out(f"opening the task for {args.record}…")
        scope = None
        if await click_task_row(page, args.record):
            await page.wait_for_timeout(9000)
            scope = await form_frame(page)
        if scope is None:
            out("no task form from the dashboard row; opening the record instead…")
            if not await dom_click(page, args.record):
                out(f"could not find a task/record link for {args.record}")
                return 1
            await page.wait_for_timeout(8000)

        scope = await form_frame(page)
        if scope is None:
            out("no form on the record page; opening the record's active workflow task…")
            if await click_task_link(page):
                await page.wait_for_timeout(9000)
                scope = await form_frame(page)

        if scope is None:
            out("task form not found (no disposition control) — dumping page")
            out(f"  url: {page.url}")
            try:
                body = (await page.inner_text("body"))[:1500]
            except Exception as exc:
                body = f"<{exc}>"
            out(f"  top frame text: {body!r}")
            for index, frame in enumerate(page.frames):
                try:
                    text = (await frame.inner_text("body"))[:300]
                except Exception:
                    continue
                out(f"  frame[{index}] {frame.url[:110]}\n      {text!r}")
            try:
                await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_noform.png"), full_page=True)
            except Exception:
                pass
            return 1

        scope_index = page.frames.index(scope) if scope in page.frames else None
        before = await read_form(scope)
        out(f"form frame: {scope_index}  url: {scope.url[:100]}")
        out(f"form before: {before}")
        for index, label, onclick in await submit_controls(page):
            out(f"  submit candidate frame[{index}]: {label}  onclick={onclick!r}")
        if args.inspect and not args.apply:
            out("inspect only: no fields changed, nothing submitted")
            return 0
        if scope_index is not None and not await submit_controls(page, exclude=scope_index):
            out("no submit control found outside the form frame")
        del scope_index

        if before["disposition_options"] and args.disposition not in before["disposition_options"]:
            out(f"disposition {args.disposition!r} is not offered: {before['disposition_options']}")
            return 1
        out(f"setting disposition = {args.disposition!r}")
        out(f"  -> {await set_field(scope, DISPOSITION_SELECT, args.disposition)}")
        out(f"setting status date = {status_date!r}")
        out(f"  -> {await set_field(scope, STATUS_DATE_INPUT, status_date)}")
        if args.note:
            out(f"setting note -> {await set_field(scope, NOTE_TEXTAREA, args.note)}")
        # Department/Staff: leave prefilled values; only fill if empty.
        if before.get("department_value") in (None, ""):
            out("department was empty: selecting the first non-placeholder option")
            await scope.evaluate(
                """(name) => { const el = document.querySelector(`[name="${name}"]`);
                    if (!el) return null;
                    const opt = [...el.options].find(o => o.value);
                    if (opt) { el.value = opt.value; el.dispatchEvent(new Event('change', {bubbles:true})); }
                    return el.value; }""",
                DEPARTMENT_SELECT,
            )
        if before.get("staff_value") in (None, ""):
            await scope.evaluate(
                """(name) => { const el = document.querySelector(`[name="${name}"]`);
                    if (!el) return null;
                    const opt = [...el.options].find(o => o.value);
                    if (opt) { el.value = opt.value; el.dispatchEvent(new Event('change', {bubbles:true})); }
                    return el.value; }""",
                STAFF_SELECT,
            )

        after_fields = await read_form(scope)
        out(f"form after: {after_fields}")
        out("pressing Submit…")
        pressed = await press_submit(page, None)
        out(f"  submit pressed: {pressed}")
        await page.wait_for_timeout(9000)
        try:
            await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_accept_after.png"))
        except Exception:
            pass
        for index, frame in enumerate(page.frames):
            try:
                text = await frame.inner_text("body")
            except Exception:
                continue
            if "Task Details" in text:
                out(f"  WARNING frame[{index}] still shows a Task Details form")
        out("submitted; verify by re-reading the record's task list")
    finally:
        try:
            await solari.close(browser)
        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
