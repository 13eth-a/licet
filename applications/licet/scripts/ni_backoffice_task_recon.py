"""read-only: what does the back office's application acceptance task expose?"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

AV_URL = "https://nullisland-test-av.accela.com/"
USER, PASSWORD = "developer", "accela"
SHOTS = os.path.join("logs", "ni_backoffice")
# never click these: they are the write actions this recon exists to describe
FORBIDDEN_CLICKS = ("issue", "accept", "save", "submit", "continue", "approve",
                    "reject", "delete", "update", "post", "record issued")
INTERESTING = ("issue", "issued", "accept", "workflow", "status", "task",
               "action", "required", "complete", "condition", "fee", "inspection")


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


async def safe_click(page, text: str) -> bool:
    """click a *navigation* element whose text matches, in any frame"""
    for scope in (page, *page.frames):
        try:
            candidates = scope.get_by_text(text, exact=False)
            count = await candidates.count()
        except Exception as exc:
            out(f"    text lookup failed in a scope: {exc}")
            continue
        for index in range(min(count, 6)):
            locator = candidates.nth(index)
            try:
                if not await locator.is_visible():
                    continue
                tag = str(await locator.evaluate("e => e.tagName")).lower()
            except Exception as exc:
                out(f"    candidate {index} inspection failed: {exc}")
                continue
            if tag in {"button", "input", "select"}:
                out(f"    skipping write control <{tag}> matching {text!r}")
                continue
            try:
                await locator.click()
                out(f"    clicked <{tag}> for {text!r}")
                return True
            except Exception as exc:
                out(f"    click failed on <{tag}>: {exc}")
    return False


async def dom_click(page, text: str) -> bool:
    """click a navigation element by dom dispatch, skipping write controls"""
    script = """(needle) => {
        const all = [...document.querySelectorAll('*')].filter(e => {
            const tag = (e.tagName || '').toUpperCase();
            if (tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT') return false;
            if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'HTML' || tag === 'BODY') return false;
            return ((e.innerText || '').trim()).includes(needle);
        });
        if (!all.length) return null;
        // Shortest innerText that still contains the needle = the innermost
        // element, not the whole page container.
        all.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
        let el = all[0];
        const anchor = el.querySelector('a');
        if (anchor) el = anchor;
        const label = (el.innerText || '').trim().slice(0, 50);
        el.click();
        return el.tagName + '|' + label;
    }"""
    for index, scope in enumerate((page, *page.frames)):
        try:
            tag = await scope.evaluate(script, text)
        except Exception as exc:
            out(f"    dom_click scope {index} failed: {exc}")
            continue
        if tag:
            out(f"    dom-clicked {tag} for {text!r} in scope {index}")
            return True
    return False


async def dump(page, stamp: str, label: str) -> set[str]:
    out(f"\n--- {label} (url={page.url}) ---")
    collected: set[str] = set()
    for index, frame in enumerate(page.frames):
        try:
            values = await frame.eval_on_selector_all(
                "a, button, td, th, span, label, h1, h2, h3, option, input, div[role=button]",
                "els => els.map(e => (e.innerText || e.value || '').trim())"
                ".filter(t => t && t.length > 1 && t.length <= 100)",
            )
        except Exception:
            continue
        collected.update(values)
        try:
            html = await frame.content()
            with open(os.path.join(SHOTS, f"{stamp}_{label}_f{index}.html"), "w", encoding="utf-8") as stream:
                stream.write(html)
        except Exception:
            pass
    interesting = sorted({v for v in collected if any(w in v.lower() for w in INTERESTING)})
    out(f"  interesting text ({len(interesting)}): {interesting[:60]}")
    return collected


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default="BLD26-00468")
    parser.add_argument("--task", default="Application Acceptance")
    args = parser.parse_args()

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
        user = await find_first_visible(page, ['input[name="username"]', 'input[id*="user" i]', 'input[type="text"]'])
        password = await find_first_visible(page, ['input[type="password"]'])
        if user is None or password is None:
            out("login inputs not found — aborting (read-only run)")
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
        before = await dump(page, stamp, "dashboard")

        opened = await safe_click(page, args.task) or await dom_click(page, args.task)
        out(f"task click ({args.task!r}): {opened}")
        await page.wait_for_timeout(7000)
        after_task = await dump(page, stamp, "task_detail")
        if not (after_task - before):
            out("  task label did not change the page; trying the record id")
            clicked = await safe_click(page, args.record) or await dom_click(page, args.record)
            out(f"record click ({args.record!r}): {clicked}")
            await page.wait_for_timeout(7000)
            await dump(page, stamp, f"record_{args.record.replace('-', '_')}")

        try:
            await page.screenshot(path=os.path.join(SHOTS, f"{stamp}_task_recon.png"))
        except Exception:
            pass
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
