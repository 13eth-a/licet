"""tests for the apply flow's local, operator visible browser"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from licet.browser import apply_browser as ab


class FakeBrowser:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class FakeChromium:
    """records every launch() option set; can be made to fail on a given key"""

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self.fail_on = fail_on
        self.browsers: list[FakeBrowser] = []

    async def launch(self, **options):
        self.calls.append(options)
        for key in self.fail_on:
            if key in options:
                raise RuntimeError(f"launch refuses {key}")
        browser = FakeBrowser()
        self.browsers.append(browser)
        return browser


class FakeDriver:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


def local_session(chromium: FakeChromium, *, headless: bool = False,
                  **kwargs) -> tuple[ab.LocalChromeSession, FakeDriver]:
    driver = FakeDriver(chromium)
    session = ab.LocalChromeSession(
        headless=headless,
        driver_factory=lambda: asyncio.sleep(0, driver),
        **kwargs,
    )
    return session, driver


def test_prefers_an_installed_chrome_at_the_expected_path():
    options = ab.chrome_launch_options(
        headless=False, environ={}, is_file=lambda _p: True,
    )
    assert options["executable_path"] == ab.DEFAULT_CHROME_EXECUTABLE
    assert "channel" not in options


def test_env_override_wins_over_the_default_path():
    options = ab.chrome_launch_options(
        headless=False, environ={ab.CHROME_PATH_ENV: "/opt/chrome"},
        is_file=lambda path: path == "/opt/chrome",
    )
    assert options["executable_path"] == "/opt/chrome"


def test_falls_back_to_the_chrome_channel_when_no_binary_is_on_disk():
    options = ab.chrome_launch_options(
        headless=False, environ={}, is_file=lambda _p: False,
    )
    assert options["channel"] == "chrome"
    assert "executable_path" not in options


def test_headless_flag_is_carried_through():
    shown = ab.chrome_launch_options(
        headless=False, environ={}, is_file=lambda _p: False,
    )
    hidden = ab.chrome_launch_options(
        headless=True, environ={}, is_file=lambda _p: False,
    )
    assert shown["headless"] is False
    assert hidden["headless"] is True


def test_default_driver_is_the_local_browser():
    assert ab.apply_browser_choice({}) == ab.LOCAL


def test_only_an_explicit_solari_opt_in_selects_the_remote_browser():
    assert ab.apply_browser_choice({ab.APPLY_BROWSER_ENV: " solari "}) == ab.SOLARI
    assert ab.apply_browser_choice({ab.APPLY_BROWSER_ENV: "SoLaRi"}) == ab.SOLARI


def test_unknown_driver_values_fail_closed_to_local():
    """a typo must not silently drop the human out of the loop"""
    assert ab.apply_browser_choice({ab.APPLY_BROWSER_ENV: "solar"}) == ab.LOCAL
    assert ab.apply_browser_choice({ab.APPLY_BROWSER_ENV: "cloud"}) == ab.LOCAL


def test_channel_launch_failure_retries_with_the_bundled_browser():
    chromium = FakeChromium(fail_on=("channel",))

    browser = asyncio.run(ab.launch_chromium(
        chromium, headless=False, environ={}, is_file=lambda _p: False,
    ))

    assert isinstance(browser, FakeBrowser)
    assert chromium.calls[0]["channel"] == "chrome"
    assert "channel" not in chromium.calls[1]


def test_executable_launch_failure_is_raised_not_downgraded():
    """an explicit chrome path failing is a real error, not a reason to swap"""
    chromium = FakeChromium(fail_on=("executable_path",))

    with pytest.raises(RuntimeError):
        asyncio.run(ab.launch_chromium(
            chromium, headless=False, environ={}, is_file=lambda _p: True,
        ))

    assert len(chromium.calls) == 1


def test_start_returns_a_headed_browser_and_close_releases_everything():
    chromium = FakeChromium()
    session, driver = local_session(chromium)

    browser = asyncio.run(session.start())
    assert browser is session.browser
    assert chromium.calls[0]["headless"] is False

    asyncio.run(session.close())
    assert browser.close_calls == 1
    assert driver.stop_calls == 1


def test_a_failed_launch_does_not_leak_the_driver():
    chromium = FakeChromium(fail_on=("channel", "executable_path"))
    session, driver = local_session(
        chromium, headless=False, is_file=lambda _p: True,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(session.start())

    assert driver.stop_calls == 1
    assert session.browser is None


def test_close_is_idempotent():
    chromium = FakeChromium()
    session, driver = local_session(chromium)

    browser = asyncio.run(session.start())
    asyncio.run(session.close())
    asyncio.run(session.close())

    assert browser.close_calls == 1
    assert driver.stop_calls == 1


def test_apply_browser_is_headed_local_by_default():
    chromium = FakeChromium()
    session, driver = local_session(chromium)

    async def run():
        browser, close = await ab.open_apply_browser(environ={}, session=session)
        await close()
        return browser

    browser = asyncio.run(run())

    assert chromium.calls[0]["headless"] is False
    assert browser.close_calls == 1
    assert driver.stop_calls == 1


def test_solari_opt_in_warns_that_no_human_can_reach_it():
    """the exact failure that stalled the handoff must be reported up front"""
    warnings: list[str] = []
    solari_browser = FakeBrowser()

    class FakeSolari:
        def __init__(self) -> None:
            self.close_calls = 0

        async def launch(self):
            return solari_browser

        async def close(self) -> None:
            self.close_calls += 1

    solari = FakeSolari()

    async def factory():
        return solari

    async def run():
        browser, close = await ab.open_apply_browser(
            environ={ab.APPLY_BROWSER_ENV: "solari"},
            solari_factory=factory, warn=warnings.append,
        )
        assert browser is solari_browser
        await close()

    asyncio.run(run())

    assert warnings == [ab.REMOTE_HANDOFF_WARNING]
    assert solari_browser.close_calls == 1
    assert solari.close_calls == 1


PS = """  693 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  948 /Applications/Google Chrome.app/Frameworks/Helper --type=gpu-process --user-data-dir=/tmp/x
26571 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/var/f/xyz --remote-debugging-pipe
  702 /usr/bin/osascript -e something
"""


class FakeRun:
    """stands in for subprocess.run, recording every command"""

    def __init__(self, ps_output: str = PS, returncode: int = 0,
                 raises: bool = False) -> None:
        self.ps_output = ps_output
        self.returncode = returncode
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(list(args))
        if self.raises:
            raise OSError("no subprocess in this environment")
        if list(args)[:2] == ["ps", "-eo"]:
            return SimpleNamespace(stdout=self.ps_output, returncode=0)
        return SimpleNamespace(stdout="", returncode=self.returncode)


def test_automation_pid_skips_the_operators_chrome_and_helper_children():
    """both processes are called \"google chrome\" the throwaway profile is the only thing that separates the driven one from the operator's own"""
    assert ab.automation_chrome_pid(PS) == 26571


def test_automation_pid_is_absent_when_only_the_operators_chrome_runs():
    own_only = "  693 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
    assert ab.automation_chrome_pid(own_only) is None


def test_automation_pid_needs_a_numeric_first_field():
    assert ab.automation_chrome_pid("pid  /x/Google Chrome --user-data-dir=/t") is None


def test_surface_window_is_a_no_op_off_macos():
    run = FakeRun()
    assert ab.surface_window(run=run, platform="linux") is False
    assert run.calls == []  # never even shells out


def test_surface_window_raises_the_driven_window_and_notifies():
    run = FakeRun()

    assert ab.surface_window(run=run, platform="darwin") is True

    osascripts = [c for c in run.calls if c[0] == "osascript"]
    assert len(osascripts) == 2
    assert "unix id is 26571" in osascripts[0][2]
    assert "frontmost" in osascripts[0][2]


def test_surface_window_does_nothing_when_no_driven_chrome_exists():
    run = FakeRun(ps_output="  693 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n")
    assert ab.surface_window(run=run, platform="darwin") is False
    assert [c for c in run.calls if c[0] == "osascript"] == []


def test_surface_window_failure_never_escapes_into_the_run():
    """a run must not die because a window could not be raised"""
    assert ab.surface_window(run=FakeRun(raises=True), platform="darwin") is False
    assert ab.surface_window(run=FakeRun(returncode=1), platform="darwin") is False


def test_local_driver_emits_no_warning():
    warnings: list[str] = []
    session, _driver = local_session(FakeChromium())

    async def run():
        _browser, close = await ab.open_apply_browser(
            environ={}, session=session, warn=warnings.append,
        )
        await close()

    asyncio.run(run())

    assert warnings == []
