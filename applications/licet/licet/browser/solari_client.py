"""async, aca aware browser client over the solari (playwright) page api"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Sequence
from urllib.parse import urlparse

from licet.browser import accela
from licet.browser.errors import BrowserError, ToolError, classify_error, tool_error
from licet.browser.accela import FlowPosition, FrameInfo
from licet.config import SOLARI_API_KEY, SOLARI_BASE_URL

_BARE_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _selector_text(value: str) -> str:
    """quote text for playwright's selector extensions safely"""
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")


def _safe_diagnostic(value: str | None, *, limit: int = 240) -> str | None:
    """bound free form browser diagnostics and redact urls/credential values"""
    if not value:
        return value
    value = re.sub(r"https?://[^\s'\"<>]+", "[url]", value)
    value = re.sub(
        r"(?i)\b(password|passwd|token|secret|authorization|cookie|api[_-]?key|credential)(\s*[:=]\s*)[^,\s;]+",
        r"\1\2[redacted]", value,
    )
    return value[:limit]


MAX_ACTION_ATTEMPTS = 3
DEFAULT_INCLUDES = ("text", "form", "errors", "frames", "notices")
ALLOWED_INCLUDES = frozenset((*DEFAULT_INCLUDES, "html"))


# observe live dom properties: serialized html does not include current values, checked state, or native
# select state
def _safe_diagnostic(value: str | None, *, limit: int = 240) -> str | None:
    """bound free form browser diagnostics and redact urls/credential values"""
    if not value:
        return value
    value = re.sub(r"https?://[^\s'\"<>]+", "[url]", value)
    value = re.sub(
        r"(?i)\b(password|passwd|token|secret|authorization|cookie|api[_-]?key|credential)(\s*[:=]\s*)[^,\s;]+",
        r"\1\2[redacted]", value,
    )
    return value[:limit]


_ACTION_STATE_JS = r"""() => {
    const visible = e => !!e.getClientRects().length &&
        getComputedStyle(e).visibility !== 'hidden';
    const text = document.body?.innerText || '';
    const controls = [...document.querySelectorAll('input,select,textarea')]
        .filter(visible).map(e => [e.id, e.name, e.type, e.value,
            e.checked, e.disabled, e.selectedIndex]);
    const sections = [...document.querySelectorAll(
        '[aria-selected],[aria-expanded],[aria-current],[role="dialog"],dialog')]
        .filter(visible).map(e => [e.id, e.getAttribute('role'),
            e.getAttribute('aria-selected'), e.getAttribute('aria-expanded'),
            e.getAttribute('aria-current'), e.textContent]);
    const manager = window.Sys?.WebForms?.PageRequestManager?.getInstance();
    if (manager && !window.__licetPostbackObserver) {
        window.__licetPostbackObserver = {completed: 0};
        manager.add_endRequest(() => window.__licetPostbackObserver.completed++);
    }
    const busy = document.readyState !== 'complete' || [...document.querySelectorAll('[aria-busy="true"]')].some(visible)
        || !!(window.Sys?.WebForms?.PageRequestManager?.getInstance()?.get_isInAsyncPostBack())
        || /\bloading\s*\.\.\./i.test(text);
    return {text, controls, sections, busy, document_id: performance.timeOrigin,
        postbacks_completed: window.__licetPostbackObserver?.completed || 0};
}"""


class PageLike(Protocol):
    """the subset of the playwright page api this client uses"""

    url: str
    frames: Sequence[Any]
    keyboard: Any

    async def goto(self, url: str, **kwargs: Any) -> Any: ...
    async def title(self) -> str: ...
    async def content(self) -> str: ...
    def locator(self, selector: str) -> Any: ...
    async def wait_for_load_state(self, state: str = ...) -> None: ...
    async def wait_for_timeout(self, milliseconds: float) -> None: ...
    async def evaluate(self, script: str) -> Any: ...
    async def screenshot(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Target:
    """how to find an element: semantic first, selector as a last resort"""

    selector: str | None = None
    text: str | None = None
    label: str | None = None
    frame: str | None = None

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> Target:
        by = str(args.get("by") or "selector").lower()
        value = args.get("target")
        value = None if value is None else str(value)
        frame = args.get("frame")
        if by == "text":
            return cls(text=value, frame=frame)
        if by == "label":
            return cls(label=value, frame=frame)
        return cls(selector=value, frame=frame)

    def describe(self) -> str:
        for kind in ("label", "text", "selector"):
            value = getattr(self, kind)
            if value:
                return f"{kind}={value}"
        return "unset"

    def candidates(self) -> list[str]:
        """playwright selectors to try, exact/most semantic first"""
        candidates: list[str] = []
        if self.label:
            label = _selector_text(self.label)
            candidates += [
                f"[fieldname='{label}']",
                f"[aria-label='{label}']",
                f"#{self.label}" if _BARE_ID_RE.match(self.label) else f'[id="{label}"]',
                f"label:text-is('{label}')",
                f"label:has-text('{label}')",
            ]
        if self.text:
            text = _selector_text(self.text)
            candidates += [
                f"a:text-is('{text}')",
                f"button:text-is('{text}')",
                f"a[title='{text}']",
                f"button[title='{text}']",
                f"[value='{text}']",
                f"a:has-text('{text}')",
                f"button:has-text('{text}')",
                f":text('{text}')",
            ]
        if self.selector:
            selector = self.selector.strip()
            # callers hand us bare aca control ids far more often than css
            if _BARE_ID_RE.match(selector):
                candidates += [f"#{selector}", f'[id="{selector}"]']
            candidates.append(selector)
        return candidates


@dataclass
class TargetDiagnostics:
    """why a target did not resolve the difference the planner needs"""

    searched: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)

    def explain(self, target: Target) -> str:
        parts = [
            f"no visible element for {target.describe()} "
            f"across {len(self.frames)} frame(s)"
        ]
        if self.hidden:
            parts.append(f"present but not visible: {self.hidden}")
        if self.absent:
            parts.append(f"no match: {self.absent}")
        if self.ambiguous:
            parts.append(f"multiple matches (refusing to guess): {self.ambiguous}")
        return "; ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "searched": self.searched,
            "hidden": self.hidden,
            "absent": self.absent,
            "ambiguous": self.ambiguous,
            "frames": self.frames,
        }


@dataclass
class ToolResult:
    ok: bool
    url: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: ToolError | None = None
    attempts: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.ok,
            "url": self.url,
            "data": self.data,
            "error": self.error.as_dict() if self.error else None,
            "attempts": self.attempts,
        }


class SolariClient:
    """per page operations"""

    def __init__(
        self,
        page: PageLike,
        *,
        action_timeout_ms: int = 8000,
        navigation_timeout_ms: int = 45000,
        settle_ms: int = 700,
        max_attempts: int = MAX_ACTION_ATTEMPTS,
        verification_timeout_ms: int = 10000,
        verification_poll_ms: int = 100,
    ) -> None:
        self.page = page
        self.action_timeout_ms = action_timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms
        self.settle_ms = settle_ms
        self.max_attempts = max_attempts
        self.verification_timeout_ms = max(0, verification_timeout_ms)
        self.verification_poll_ms = max(1, verification_poll_ms)


    def _url(self) -> str | None:
        return getattr(self.page, "url", None)

    def _frames(self) -> list[Any]:
        frames = list(getattr(self.page, "frames", []) or [])
        return frames or [self.page]

    async def _neutralize_mask(self) -> None:
        """hide the overlay that eats clicks; best effort per frame"""
        for frame in self._frames():
            try:
                await frame.evaluate(accela.MASK_NEUTRALIZER_JS)
            except Exception:
                continue

    async def settle(self) -> None:
        """wait for an aca postback to finish, then restore clickability"""
        try:
            await self.page.wait_for_load_state("load")
        except Exception:
            pass
        try:
            await self.page.wait_for_timeout(self.settle_ms)
        except Exception:
            pass
        await self._neutralize_mask()

    async def _find_frame(self, marker: str | None) -> Any | None:
        if not marker:
            return None
        for frame in self._frames():
            if marker.lower() in (getattr(frame, "url", "") or "").lower():
                return frame
        return None

    async def _resolve(
        self, target: Target
    ) -> tuple[Any | None, Any | None, int, TargetDiagnostics]:
        """return (frame, locator, match_count, diagnostics) for the target"""
        frames = self._frames()
        preferred = await self._find_frame(target.frame)
        if target.frame and preferred is None:
            diagnostics = TargetDiagnostics(
                searched=list(target.candidates()),
                frames=[getattr(frame, "url", "") or "" for frame in frames],
            )
            diagnostics.absent.append(f"frame containing {target.frame!r}")
            return None, None, 0, diagnostics
        if preferred is not None:
            # an explicit frame is a constraint, not merely a search hint
            frames = [preferred] if target.frame else [preferred, *[f for f in frames if f is not preferred]]
        diagnostics = TargetDiagnostics(
            searched=list(target.candidates()),
            frames=[getattr(frame, "url", "") or "" for frame in frames],
        )
        for frame in frames:
            for selector in target.candidates():
                try:
                    locator = frame.locator(selector)
                    count = await locator.count()
                except Exception:
                    continue
                if not count:
                    if selector not in diagnostics.hidden and selector not in diagnostics.absent:
                        diagnostics.absent.append(selector)
                    continue
                if count > 1:
                    # aca keeps hidden copies of controls in the dom
                    try:
                        visible_locator = locator.filter(visible=True)
                        visible_count = await visible_locator.count()
                    except Exception:
                        visible_count = count
                    if visible_count == 0:
                        diagnostics.hidden.append(selector)
                        continue
                    if visible_count > 1:
                        diagnostics.ambiguous.append(f"{selector} ({visible_count} visible)")
                        continue
                    locator, count = visible_locator, visible_count
                first = locator.first
                visible = True
                try:
                    visible = await first.is_visible()
                except Exception:
                    visible = True
                if not visible:
                    if selector not in diagnostics.hidden:
                        diagnostics.hidden.append(selector)
                    continue
                return frame, first, count, diagnostics
        return None, None, 0, diagnostics

    @staticmethod
    def _unresolved(target: Target, diagnostics: TargetDiagnostics) -> ToolError:
        kind = (
            BrowserError.AMBIGUOUS_TARGET
            if diagnostics.ambiguous
            else BrowserError.NOT_ACTIONABLE if diagnostics.hidden else BrowserError.NOT_FOUND
        )
        return ToolError(kind, diagnostics.explain(target))

    async def _attribute(self, locator: Any, name: str) -> str:
        try:
            return (await locator.get_attribute(name)) or ""
        except Exception:
            return ""

    async def _is_masked(self, locator: Any) -> bool:
        classes = await self._attribute(locator, "class")
        return accela.MASKED_CLASS_MARKER in classes.lower()

    async def _readback(self, locator: Any) -> str:
        try:
            return await locator.input_value()
        except Exception:
            return ""


    async def navigate(self, url: str) -> ToolResult:
        last: ToolError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                await self.page.goto(
                    url, timeout=self.navigation_timeout_ms, wait_until="domcontentloaded"
                )
                await self.settle()
                html = await self._content()
                lowered_html = html.lower()
                if "does not exist" in lowered_html and "file" in lowered_html:
                    return ToolResult(
                        ok=False,
                        url=self._url(),
                        attempts=attempt,
                        error=ToolError(
                            BrowserError.PORTAL_ERROR,
                            f"ACA error page at {url} (check the URL shape, not just the host)",
                        ),
                    )
                # login/session notices are js dialog text and do not redirect
                if "login" not in (self._url() or "").lower():
                    visible = await _page_text(self.page)
                    notices = accela.detect_notices(visible)
                    if notices:
                        return ToolResult(
                            ok=False,
                            url=self._url(),
                            attempts=attempt,
                            data={"notices": notices},
                            error=ToolError(
                                BrowserError.AUTH_REQUIRED,
                                "portal notice: " + ", ".join(notices),
                            ),
                        )
                return ToolResult(ok=True, url=self._url(), attempts=attempt)
            except Exception as exc:  # noqa: BLE001 classified immediately
                last = tool_error(str(exc))
                if last.retryable and attempt < self.max_attempts:
                    await asyncio.sleep(0.4 * attempt)
                    continue
                return ToolResult(ok=False, url=self._url(), attempts=attempt, error=last)
        return ToolResult(ok=False, url=self._url(), attempts=self.max_attempts, error=last)

    async def _action_state(self) -> dict[str, Any]:
        frames = self._frames()
        async def observe(frame):
            # solari uses patchright, whose default isolated world cannot see the page's asp.net runtime
            options = {"isolated_context": False} if "isolated_context" in inspect.signature(frame.evaluate).parameters else {}
            return await frame.evaluate(_ACTION_STATE_JS, **options)

        states = await asyncio.gather(*(observe(frame) for frame in frames))
        if not all(isinstance(state, dict) for state in states):
            raise RuntimeError("browser did not return an action-state snapshot")
        return {"url": self._url(), "frames": [
            {"url": getattr(frame, "url", ""), **state}
            for frame, state in zip(frames, states)
        ]}

    @staticmethod
    def _state_signature(state: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    async def _verify_action(
        self, before: dict[str, Any],
        predicate: Callable[[], Awaitable[bool]] | None = None,
        postback_frame: Any = None,
    ) -> dict[str, Any]:
        """bounded observation only; never dispatch an action from recovery"""
        started = time.monotonic()
        deadline = started + self.verification_timeout_ms / 1000
        samples = 0
        observation_attempts = 0
        previous = None
        last = None
        observation_error = None
        observation_error_type = None
        observation_timed_out = False
        last_changed = False
        last_matched = False
        last_ready = False
        last_frame_status: list[dict[str, Any]] | None = None
        stable_samples = 0
        while True:
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    observation_attempts += 1
                    after = await self._action_state()
                    samples += 1
                    last = self._state_signature(after)
                    last_changed = after != before
                    matched = await predicate() if predicate else last_changed
                    if postback_frame is not None:
                        frame_index = postback_frame
                        old = before["frames"][frame_index]
                        new = after["frames"][frame_index]
                        completed = (new.get("document_id") != old.get("document_id")
                                     or new.get("postbacks_completed", 0) > old.get("postbacks_completed", 0))
                        matched = matched and completed
                last_frame_status = [
                    {
                        "busy": bool(frame.get("busy")),
                        "document_changed": frame.get("document_id") != before["frames"][index].get("document_id"),
                        "postbacks_completed_delta": max(
                            0, int(frame.get("postbacks_completed", 0) or 0)
                            - int(before["frames"][index].get("postbacks_completed", 0) or 0),
                        ),
                    }
                    for index, frame in enumerate(after["frames"])
                    if index < len(before["frames"])
                ]
                ready = not any(f["busy"] for f in after["frames"])
                last_matched = bool(matched)
                last_ready = ready
                if matched and ready:
                    stable_samples = stable_samples + 1 if last == previous else 1
                else:
                    stable_samples = 0
                if stable_samples >= 2:
                    return {"status": "verified", "before": self._state_signature(before),
                            "after": last, "samples": samples,
                            "last_changed": last_changed, "last_matched": last_matched,
                            "last_ready": last_ready, "stable_samples": stable_samples,
                            "frame_status": last_frame_status,
                            "elapsed_ms": round((time.monotonic() - started) * 1000)}
                previous = last if matched and ready else None
            except Exception as exc:
                timed_out = isinstance(exc, TimeoutError) and time.monotonic() >= deadline
                observation_timed_out = observation_timed_out or timed_out
                # preserve the last concrete observer exception when the enclosing verification deadline
                # subsequently expires
                if not timed_out or observation_error is None:
                    observation_error = str(exc) or type(exc).__name__
                    observation_error_type = type(exc).__name__
                last_changed = False
                last_matched = False
                last_ready = False
                stable_samples = 0
                previous = None
            if time.monotonic() >= deadline:
                safe_error = _safe_diagnostic(observation_error)
                return {"status": "unverified", "before": self._state_signature(before),
                        "after": last, "observation_error": safe_error,
                        "observation_error_type": observation_error_type if observation_error else None,
                        "observation_timed_out": observation_timed_out,
                        "last_changed": last_changed, "last_matched": last_matched,
                        "last_ready": last_ready, "stable_samples": stable_samples,
                        "frame_status": last_frame_status,
                        "samples": samples, "observation_attempts": observation_attempts,
                        "elapsed_ms": round((time.monotonic() - started) * 1000)}
            await asyncio.sleep(min(self.verification_poll_ms / 1000,
                                    max(0, deadline - time.monotonic())))

    def _action_result(self, target: Target, verification: dict[str, Any],
                       error: Exception | None = None, **data: Any) -> ToolResult:
        verified = verification["status"] == "verified"
        return ToolResult(
            ok=verified, url=self._url(),
            data={"target": target.describe(), **data, "verification": verification,
                  "recovered_after_error": verified and error is not None},
            error=None if verified else ToolError(
                BrowserError.ACTION_OUTCOME_UNKNOWN,
                "Action was dispatched but its outcome could not be verified; "
                "do not replay it without reconciling portal state."
                + (f" Provider error: {_safe_diagnostic(str(error))}" if error else "")),
        )

    async def click(self, target: Target) -> ToolResult:
        try:
            frame, locator, matches, diagnostics = await self._resolve(target)
            if locator is None:
                return ToolResult(ok=False, url=self._url(),
                                  error=self._unresolved(target, diagnostics))
            target_text = target.describe().lower()
            if ((accela.POPUP_CONTINUE_ID.lower() in target_text
                 or target_text in {"text=continue", "label=continue"})
                    and accela.popup_continue_disabled(await self._content())):
                return ToolResult(ok=False, url=self._url(), error=ToolError(
                    BrowserError.NOT_ACTIONABLE,
                    "ACA scheduling Continue is disabled until a date and time are selected"))
            before = await self._action_state()
        except Exception as exc:
            return ToolResult(ok=False, url=self._url(), error=tool_error(str(exc)))
        error = None
        try:
            await locator.click(timeout=self.action_timeout_ms)
        except Exception as exc:
            error = exc
        verification = await self._verify_action(before)
        return self._action_result(target, verification, error,
                                   frame=getattr(frame, "url", ""), matches=matches, forced=False)

    async def type_text(self, target: Target, value: str) -> ToolResult:
        try:
            _, locator, _, diagnostics = await self._resolve(target)
            if locator is None:
                return ToolResult(ok=False, url=self._url(),
                                  error=self._unresolved(target, diagnostics))
            masked = await self._is_masked(locator)
            before = await self._action_state()
        except Exception as exc:
            return ToolResult(ok=False, url=self._url(), error=tool_error(str(exc)))
        error = None
        try:
            if masked:
                await locator.focus()
                await locator.press("ControlOrMeta+A")
                await locator.press("Delete")
                await self.page.keyboard.type(value, delay=70)
            else:
                await locator.fill(value, timeout=self.action_timeout_ms)
        except Exception as exc:
            error = exc
        got = None

        async def matches():
            nonlocal got
            _, current, _, _ = await self._resolve(target)
            if current is None:
                return False
            # do not turn a read failure into a matching empty string
            got = await current.input_value()
            if masked and _digits(value):
                return _digits(got) == _digits(value)
            return got == value

        verification = await self._verify_action(before, matches)
        return self._action_result(target, verification, error, value=got, masked=masked)

    async def select(self, target: Target, value: str) -> ToolResult:
        try:
            frame, locator, _, diagnostics = await self._resolve(target)
            if locator is None:
                return ToolResult(ok=False, url=self._url(),
                                  error=self._unresolved(target, diagnostics))
            options = await locator.evaluate("""e => Array.from(e.options || []).map(o =>
                ({label: o.label, value: o.value, disabled: o.disabled ||
                  (o.parentElement.tagName === 'OPTGROUP' && o.parentElement.disabled)}))""")
            candidates = [o for o in options if o["label"] == value]
            if not candidates:
                candidates = [o for o in options if o["value"] == value]
            if len(candidates) != 1 or candidates[0]["disabled"]:
                kind = BrowserError.AMBIGUOUS_TARGET if len(candidates) > 1 else BrowserError.NOT_ACTIONABLE
                return ToolResult(ok=False, url=self._url(), error=ToolError(
                    kind, "Requested option is absent, disabled, or ambiguous"))
            selected_value = candidates[0]["value"]
            # dispatch by the observed label/value once; never try the other selector after a timeout that
            # may already have fired a postback
            choice = {"label": value} if candidates[0]["label"] == value else {"value": value}
            onchange = (await locator.get_attribute("onchange")) or ""
            expects_postback = "__dopostback" in onchange.lower() or "webform_dopostback" in onchange.lower()
            postback_frame = self._frames().index(frame) if expects_postback else None
            before = await self._action_state()
        except Exception as exc:
            return ToolResult(ok=False, url=self._url(), error=tool_error(str(exc)))
        error = None
        try:
            await locator.select_option(**choice, timeout=self.action_timeout_ms)
        except Exception as exc:
            error = exc
        got = None

        async def matches():
            nonlocal got
            _, current, _, _ = await self._resolve(target)
            if current is None:
                return False
            got = await current.input_value()
            return got == selected_value

        verification = await self._verify_action(before, matches, postback_frame=postback_frame)
        return self._action_result(target, verification, error, value=value,
                                   selected_value=got, postback=expects_postback)

    async def _content(self) -> str:
        try:
            return await self.page.content()
        except Exception:
            return ""

    async def wait_for_text(
        self,
        *,
        present: str | None = None,
        absent: str | None = None,
        timeout_ms: int = 15000,
        poll_ms: int = 700,
    ) -> ToolResult:
        """poll until `present` appears or `absent` disappears"""
        elapsed = 0
        while True:
            body = await _page_text(self.page)
            settled = (present is None or present.lower() in body.lower()) and (
                absent is None or absent.lower() not in body.lower()
            )
            if settled:
                return ToolResult(
                    ok=True,
                    url=self._url(),
                    data={"present": present, "absent": absent, "waited_ms": elapsed},
                )
            if elapsed >= timeout_ms:
                return ToolResult(
                    ok=False,
                    url=self._url(),
                    data={"present": present, "absent": absent, "waited_ms": elapsed},
                    error=ToolError(
                        BrowserError.TIMEOUT,
                        f"timed out after {elapsed}ms waiting for "
                        f"present={present!r} absent={absent!r}",
                    ),
                )
            try:
                await self.page.wait_for_timeout(poll_ms)
            except Exception:
                break
            elapsed += poll_ms
        return ToolResult(
            ok=False,
            url=self._url(),
            error=ToolError(BrowserError.UNKNOWN, "wait_for_text could not poll the page"),
        )

    async def read_page(
        self, *, include: Sequence[str] | None = None, max_text: int = 4000
    ) -> ToolResult:
        """everything the planner needs to know where it is and what is missing"""
        wants = set(include or DEFAULT_INCLUDES)
        unknown = wants - ALLOWED_INCLUDES
        if unknown:
            return ToolResult(
                ok=False,
                url=self._url(),
                error=ToolError(
                    BrowserError.UNKNOWN, f"unknown include(s): {sorted(unknown)}"
                ),
            )

        fields: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        frames: list[dict[str, Any]] = []
        row_controls: list[dict[str, str]] = []
        grids: list[dict[str, Any]] = []
        texts: list[str] = []
        calendar: list[accela.CalendarMonth] = []
        selectable_times = ""
        for frame in self._frames():
            html = ""
            if {"form", "errors", "frames", "html"} & wants:
                html = await _frame_content(frame)
            from licet.phase7.grids import grid_observations
            grids.extend(grid_observations(html))
            url = getattr(frame, "url", "") or ""
            text = ""
            if "text" in wants or "notices" in wants:
                text = await _frame_text(frame)
            if text:
                texts.append(text)
            frames.append(
                FrameInfo(
                    url=url,
                    title=await _frame_title(frame),
                    popup=accela.POPUP_ID_PREFIX in html,
                    login_panel=accela.LOGIN_FRAME_MARKER in url,
                    text=text[:400],
                    errors=tuple(accela.validation_targets(html)),
                ).as_dict()
            )
            if "form" in wants:
                fields += [f.as_dict() for f in accela.parse_fields(html)]
                if accela.CALENDAR_CONTAINER_ID in html:
                    calendar += accela.parse_calendar(html)
                    selectable_times = accela.selectable_times_text(html) or selectable_times
            if "errors" in wants:
                errors += [e.as_dict() for e in accela.parse_validation_errors(html)]
            if html:
                row_controls += accela.parse_inspection_row_controls(html)
            if "html" in wants:
                frames[-1]["html"] = html[:5000]

        visible = "\n".join(texts)
        position: FlowPosition | None = accela.locate(self._url() or "", visible)
        data: dict[str, Any] = {
            "url": self._url(),
            "text": visible[:max_text],
            "truncated": len(visible) > max_text,
            # a half rendered ajax section reads as "you have not added any inspections"; flag it so that
            # is never reported as fact
            "loading": accela.detect_loading(visible),
            "flow": (
                {"flow": position.flow, "step": position.step, "page": position.page_number}
                if position
                else None
            ),
            "fields": fields,
            # the scheduling wizard's choices, parsed here because "what inspection can i book?" is a
            # question the planner asks every time
            "inspection_types": [
                option.as_dict() for option in accela.parse_inspection_types(fields)
            ],
            "inspection_type_total": accela.inspection_type_total(visible),
            "calendar": [month.as_dict() for month in calendar],
            "calendar_available": any(month.any_available for month in calendar),
            "selectable_times": selectable_times,
            "validation_errors": errors,
            "inspection_row_controls": row_controls,
            "grids": grids,
            "frames": frames,
            "popup_open": any(frame["popup"] for frame in frames),
            "notices": accela.detect_notices(visible),
        }
        popup_open = any(frame["popup"] for frame in frames)
        data["portal_findings"] = list(
            accela.detect_weirdness(
                visible,
                self._url() or "",
                popup_open=popup_open,
                loading=data["loading"],
                notices=data["notices"],
            )
        )
        page_identity: dict[str, Any] = {
            "url_path": (urlparse(self._url() or "").path or None) if self._url() else None,
        }
        ref = accela.parse_ref_from_url(self._url() or "")
        if ref:
            page_identity["record_number"] = "/".join(
                ref[key] for key in ("capID1", "capID2", "capID3")
            )
        data["page_identity"] = page_identity
        return ToolResult(ok=True, url=self._url(), data=data)

    async def screenshot(self, path: str | None = None) -> ToolResult:
        try:
            if path:
                await self.page.screenshot(path=path)
            else:
                await self.page.screenshot(full_page=False)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, url=self._url(), error=tool_error(str(exc)))
        return ToolResult(ok=True, url=self._url(), data={"path": path})

    async def login(self, username: str, password: str) -> ToolResult:
        """civicid sso login the credential form is an iframe, not page html"""
        nav = await self.navigate(accela.LOGIN_URL)
        if not nav.ok:
            return nav
        frame = await self._find_frame(accela.LOGIN_FRAME_MARKER)
        if frame is None:
            return ToolResult(
                ok=False,
                url=self._url(),
                error=ToolError(
                    BrowserError.NOT_FOUND,
                    f"login panel iframe ('{accela.LOGIN_FRAME_MARKER}') not found",
                ),
            )
        try:
            for selector, value in (
                ("input[name='username']", username),
                ("input[name='password']", password),
            ):
                await frame.locator(selector).first.fill(value, timeout=self.action_timeout_ms)
            try:
                await frame.locator("button").first.click(timeout=self.action_timeout_ms)
            except Exception:
                await frame.locator("input[name='password']").first.press("Enter")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, url=self._url(), error=tool_error(str(exc)))
        await self.settle()
        url, text = await self._await_login()
        return ToolResult(
            ok=True,
            url=url,
            data={
                "authenticated": accela.login_succeeded(url, text),
                "landed_on": url,
            },
        )

    async def _await_login(self, *, attempts: int = 8, poll_ms: int = 700) -> tuple[str, str]:
        """wait for the post login landing page rather than for one url change"""
        url = self._url() or ""
        text = ""
        for attempt in range(attempts):
            url = self._url() or ""
            text = await _page_text(self.page)
            if accela.login_succeeded(url, text):
                return url, text
            if attempt + 1 >= attempts:
                break
            try:
                await self.page.wait_for_timeout(poll_ms)
            except Exception:
                break
            await self._neutralize_mask()
        return url, text

    async def authenticate(self) -> ToolResult:
        """log in with the configured test account (no credentials in results)"""
        from licet.config import ACCELA_TEST_PASSWORD, ACCELA_TEST_USERNAME

        if not ACCELA_TEST_USERNAME or not ACCELA_TEST_PASSWORD:
            return ToolResult(
                ok=False,
                error=ToolError(
                    BrowserError.AUTH_REQUIRED,
                    "ACCELA_TEST_USERNAME/ACCELA_TEST_PASSWORD are not configured",
                ),
            )
        return await self.login(ACCELA_TEST_USERNAME, ACCELA_TEST_PASSWORD)


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


async def _frame_content(frame: Any) -> str:
    try:
        return await frame.content()
    except Exception:
        return ""


async def _frame_text(frame: Any) -> str:
    try:
        return await frame.locator("body").inner_text()
    except Exception:
        return ""


async def _frame_title(frame: Any) -> str:
    try:
        return await frame.title()
    except Exception:
        return ""


async def _page_text(page: Any) -> str:
    frames = list(getattr(page, "frames", []) or []) or [page]
    parts = [await _frame_text(frame) for frame in frames]
    return "\n".join(part for part in parts if part)


class SolariSession:
    """thin boundary around the real solari sdk (the only sdk import)"""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        profile_id: str | None = None,
        recording: bool | None = None,
        **launch_options: Any,
    ) -> None:
        self.api_key = api_key or SOLARI_API_KEY
        self.base_url = SOLARI_BASE_URL
        self.launch_options = dict(launch_options)
        if profile_id is not None:
            self.launch_options["profile_id"] = profile_id
        if recording is not None:
            self.launch_options["recording"] = recording
        self._solari: Any = None
        self._browser: Any = None

    async def start(self) -> Any:
        from solari_browser import Solari

        self._solari = Solari(api_key=self.api_key)
        self._browser = await self._solari.launch(**self.launch_options)
        return self._browser

    async def client(self, **client_options: Any) -> SolariClient:
        if self._browser is None:
            await self.start()
        page = await self._browser.new_page()
        return SolariClient(page, **client_options)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

    async def __aenter__(self) -> SolariClient:
        return await self.client()

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()
