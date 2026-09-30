"""the browser an apply run drives: local, headed, and visible to the operator"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

DEFAULT_CHROME_EXECUTABLE = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
CHROME_PATH_ENV = "LICET_CHROME_PATH"

# which driver the apply flow uses
APPLY_BROWSER_ENV = "LICET_APPLY_BROWSER"
LOCAL = "local"
SOLARI = "solari"

# emitted when the apply flow is pointed at a browser no human can reach
REMOTE_HANDOFF_WARNING = (
    "LICET_APPLY_BROWSER=solari: this browser renders remotely and has no "
    "live view, so no human can accept the disclaimer in it. The attestation "
    "handoff will time out. Use the local browser for apply runs."
)


def apply_browser_choice(environ: Mapping[str, str] | None = None) -> str:
    """which driver the apply flow should use"""
    source = os.environ if environ is None else environ
    raw = source.get(APPLY_BROWSER_ENV, "")
    return SOLARI if raw.strip().casefold() == SOLARI else LOCAL


def chrome_launch_options(
    *,
    headless: bool,
    environ: Mapping[str, str] | None = None,
    is_file: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """keyword arguments for `chromium.launch()`"""
    source = os.environ if environ is None else environ
    exists = (lambda path: Path(path).is_file()) if is_file is None else is_file

    options: dict[str, Any] = {"headless": headless}
    candidate = source.get(CHROME_PATH_ENV) or DEFAULT_CHROME_EXECUTABLE
    if exists(candidate):
        options["executable_path"] = candidate
    else:
        options["channel"] = "chrome"
    return options


async def launch_chromium(
    chromium: Any,
    *,
    headless: bool,
    environ: Mapping[str, str] | None = None,
    is_file: Callable[[str], bool] | None = None,
) -> Any:
    """launch a headed browser, degrading to the bundled chromium if need be"""
    options = chrome_launch_options(
        headless=headless, environ=environ, is_file=is_file,
    )
    try:
        return await chromium.launch(**options)
    except Exception:
        if "channel" not in options:
            raise
        options.pop("channel")
        return await chromium.launch(**options)


class LocalChromeSession:
    """a local browser, headed by default so the operator can see it"""

    def __init__(
        self,
        *,
        headless: bool = False,
        environ: Mapping[str, str] | None = None,
        is_file: Callable[[str], bool] | None = None,
        driver_factory: Callable[[], Awaitable[Any]] | None = None,
        close_timeout_s: float = 30.0,
    ) -> None:
        self.headless = headless
        self._environ = environ
        self._is_file = is_file
        self._driver_factory = driver_factory
        self._close_timeout_s = close_timeout_s
        self._driver: Any = None
        self.browser: Any = None

    async def _start_driver(self) -> Any:
        if self._driver_factory is not None:
            return await self._driver_factory()
        from patchright.async_api import async_playwright

        return await async_playwright().start()

    async def start(self) -> Any:
        """start the driver and browser"""
        self._driver = await self._start_driver()
        try:
            self.browser = await launch_chromium(
                self._driver.chromium, headless=self.headless,
                environ=self._environ, is_file=self._is_file,
            )
        except Exception:
            # do not leak the driver when the browser never came up
            await self.close()
            raise
        return self.browser

    async def close(self) -> None:
        """release the browser and the driver"""
        browser, self.browser = self.browser, None
        if browser is not None:
            try:
                await asyncio.wait_for(browser.close(), self._close_timeout_s)
            except Exception:
                pass
        driver, self._driver = self._driver, None
        if driver is not None:
            try:
                await asyncio.wait_for(driver.stop(), self._close_timeout_s)
            except Exception:
                pass


_NOTIFY_SCRIPT = (
    'display notification "The Licet browser window is open." '
    'with title "Licet"'
)


def _raise_script(pid: int) -> str:
    return (
        'tell application "System Events"\n'
        f'  set p to first process whose unix id is {pid}\n'
        '  set frontmost of p to true\n'
        '  try\n'
        '    perform action "AXRaise" of window 1 of p\n'
        '  end try\n'
        'end tell'
    )


def automation_chrome_pid(ps_output: str) -> int | None:
    """pick the driver's chrome out of `ps -eo pid=,command=` output"""
    for line in ps_output.splitlines():
        if "Google Chrome" not in line or "user-data-dir" not in line:
            continue
        if "--type=" in line:
            continue
        head = line.split(None, 1)[0]
        if head.isdigit():
            return int(head)
    return None


def surface_window(*, run: Callable[..., Any] | None = None,
                   platform: str | None = None) -> bool:
    """best-effort: raise the driven chrome and post a notification"""
    plat = sys.platform if platform is None else platform
    if not plat.startswith("darwin"):
        return False
    execute = subprocess.run if run is None else run
    try:
        listing = execute(["ps", "-eo", "pid=,command="],
                          capture_output=True, text=True, timeout=10)
        pid = automation_chrome_pid(getattr(listing, "stdout", "") or "")
        if pid is None:
            return False
        raised = execute(["osascript", "-e", _raise_script(pid)],
                         capture_output=True, text=True, timeout=15)
        execute(["osascript", "-e", _NOTIFY_SCRIPT],
                capture_output=True, text=True, timeout=15)
        return getattr(raised, "returncode", 1) == 0
    except Exception:
        return False


async def _solari_from_env(environ: Mapping[str, str] | None) -> Any:
    from solari_browser import Solari

    source = os.environ if environ is None else environ
    api_key = source.get("SOLARI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SOLARI_API_KEY is unset; cannot launch Solari")
    return Solari(api_key=api_key)


async def open_apply_browser(
    *,
    headless: bool = False,
    environ: Mapping[str, str] | None = None,
    is_file: Callable[[str], bool] | None = None,
    session: LocalChromeSession | None = None,
    solari_factory: Callable[[], Awaitable[Any]] | None = None,
    launch_timeout_s: float = 120.0,
    warn: Callable[[str], Any] | None = None,
) -> tuple[Any, Callable[[], Awaitable[None]]]:
    """open the browser an apply run drives, and return `(browser, close)`"""
    if apply_browser_choice(environ) == SOLARI:
        if warn is not None:
            warn(REMOTE_HANDOFF_WARNING)
        solari = (await solari_factory() if solari_factory is not None
                  else await _solari_from_env(environ))
        browser = await asyncio.wait_for(solari.launch(), launch_timeout_s)

        async def close_remote() -> None:
            try:
                await asyncio.wait_for(browser.close(), 30)
            except Exception:
                pass
            try:
                await solari.close()
            except Exception:
                pass

        return browser, close_remote

    local = session or LocalChromeSession(
        headless=headless, environ=environ, is_file=is_file,
    )
    browser = await local.start()
    return browser, local.close


__all__ = [
    "APPLY_BROWSER_ENV", "CHROME_PATH_ENV", "DEFAULT_CHROME_EXECUTABLE",
    "LOCAL", "REMOTE_HANDOFF_WARNING", "SOLARI",
    "LocalChromeSession", "apply_browser_choice", "automation_chrome_pid",
    "chrome_launch_options", "launch_chromium", "open_apply_browser",
    "surface_window",
]
