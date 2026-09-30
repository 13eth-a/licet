"""Record one real UI-triggered, plan-only Accela run. No account secrets in media."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from patchright.async_api import async_playwright
from scripts.phase9_screen_recorder import ScreenRecorder

OUT = Path(os.environ.get('LICET_UI_FILM_OUTPUT', ROOT / 'logs/licet-connected-walkthrough'))
ORIGIN = os.environ.get('LICET_UI_ORIGIN', 'http://127.0.0.1:8766')


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    token = (ROOT / 'logs/ui-bridge/session-key.txt').read_text().strip()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            headless=True,
        )
        context = await browser.new_context(viewport={'width': 1440, 'height': 900}, device_scale_factor=1)
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        try:
            await cdp.send('Browser.setPermission', {
                'permission': {'name': 'local-network-access'}, 'setting': 'granted',
                'origin': ORIGIN,
            })
        except Exception:
            pass  # Older Chrome may not implement the permission.
        print('Opening live UI.', flush=True)
        await asyncio.wait_for(page.goto(ORIGIN + '/#connect=' + token, wait_until='domcontentloaded'), 60)
        await asyncio.sleep(8)
        print('UI visible:', (await page.locator('body').inner_text())[:1800], flush=True)
        await page.screenshot(path=str(OUT/'connection-check.png'))
        await asyncio.wait_for(page.get_by_text('Connected to local agent', exact=True).wait_for(), 25)
        await page.screenshot(path=str(OUT/'01-connected-home.png'))
        print('UI connected to private agent bridge.', flush=True)
        if os.environ.get('LICET_UI_CHECK_ONLY') == '1':
            await browser.close()
            return
        recorder = ScreenRecorder(page, OUT/'ui-screen')
        await recorder.start()
        marks = {'started': time.time(), 'segments': []}
        await asyncio.sleep(4)
        await page.get_by_role('button', name='Run agent').click()
        marks['run_clicked'] = time.time()
        print('Started real run through UI button.', flush=True)
        await asyncio.sleep(4)
        await page.get_by_role('button', name='Watch browser').click()
        await page.get_by_role('button', name='Expand', exact=False).click()
        deadline = time.monotonic() + 1100
        state = None
        while time.monotonic() < deadline:
            state = await page.evaluate("""async () => {
                const key = sessionStorage.getItem('licet-bridge-key');
                const r = await fetch('http://127.0.0.1:8765/status', {headers:{Authorization:'Bearer '+key}});
                return r.json();
            }""")
            if state.get('state') in ('finished', 'error'):
                break
            print('Recording:', state.get('state'), 'authenticated=' + str(state.get('authenticated')),
                  'semantic_steps=' + str(len(state.get('trace', []))), flush=True)
            await asyncio.sleep(10)
        await page.get_by_role('button', name='Close fullscreen').click()
        await page.get_by_role('button', name='Return to trace').click()
        await asyncio.sleep(3)
        await page.screenshot(path=str(OUT/'02-run-trace.png'), full_page=True)
        if state.get('report'):
            await asyncio.wait_for(page.locator('.result-panel').scroll_into_view_if_needed(), 5)
        await asyncio.sleep(5)
        await page.screenshot(path=str(OUT/'03-run-outcome.png'))
        await page.get_by_role('button', name='Permits', exact=False).first.click()
        await asyncio.sleep(2)
        await page.get_by_role('button', name='Calendar', exact=True).click()
        await asyncio.sleep(4)
        await page.screenshot(path=str(OUT/'04-calendar-evidence.png'))
        if os.environ.get('LICET_RECORD_REPLAY') == '1':
            marks['replay_started'] = time.time()
            await page.get_by_role('button', name='Booking replay', exact=False).first.click()
            await asyncio.sleep(6)
            await page.get_by_role('button', name='Run booking replay', exact=True).click()
            await asyncio.wait_for(page.get_by_test_id('replay-verification').wait_for(), 60)
            await asyncio.sleep(7)
            await page.screenshot(path=str(OUT/'05-replay-captured-calendar.png'))
            await page.get_by_role('button', name='Injected slot', exact=True).click()
            await asyncio.sleep(10)
            await page.screenshot(path=str(OUT/'06-replay-verified.png'))
            evidence = await page.evaluate("""async () => {
                const key = sessionStorage.getItem('licet-bridge-key');
                return (await fetch('http://127.0.0.1:8765/booking-replay', {
                    method:'POST', headers:{Authorization:'Bearer '+key}
                })).json();
            }""")
            (OUT/'replay-evidence.json').write_text(json.dumps(evidence, indent=2)+'\n')
        marks['finished'] = time.time()
        marks['run_id'] = state.get('run_id')
        (OUT/'recording.json').write_text(json.dumps(marks, indent=2)+'\n')
        (OUT/'final-status.json').write_text(json.dumps(state, indent=2)+'\n')
        await recorder.stop()
        await browser.close()
        print('Recording saved:', OUT, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
