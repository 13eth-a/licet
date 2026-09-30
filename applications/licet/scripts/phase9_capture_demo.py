"""Capture one plan-only flagship using the unchanged product capabilities.

Raw authenticated browser media stays in ignored logs/. Do not publish raw
recordings without reviewing account/contact information. No mutation is enabled.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from licet.browser.solari_client import SolariSession
import licet.eval.phase5_live as wiring
from scripts import ni_phase5_acceptance as harness
from scripts.phase9_screen_recorder import ScreenRecorder

OUT = Path(os.environ.get('LICET_CAPTURE_OUTPUT', 'logs/phase9-calendar-search-20260928'))
GOAL = os.environ.get('LICET_CAPTURE_GOAL', 'Get permit 000000014 ready for its next inspection without paying anything or signing anything.')


class CaptureSession(SolariSession):
    def __init__(self):
        # Explicit user approval received for this one authenticated recording.
        super().__init__(recording=False)

    async def start(self):
        browser = await super().start()
        (OUT/'session.json').write_text(json.dumps({'session_id':browser.id,'expires_at':browser.expires_at})+'\n')
        return browser

    async def close(self):
        try:
            try:
                if getattr(self, "screen_recorder", None):
                    await self.screen_recorder.stop()
            finally:
                await super().close()
            # The local continuous screencast is the video; no second server-side replay.
        except Exception as exc:
            print('Recording finalization:',type(exc).__name__,flush=True)


async def main():
    from urllib.parse import urlsplit
    from licet.browser import accela
    if urlsplit(accela.PORTAL_ROOT).hostname != "aca-test.accela.com":
        raise ValueError("demo recording requires the exact Accela sandbox host")
    OUT.mkdir(parents=True,exist_ok=True)
    wiring.SolariSession = CaptureSession
    factory=harness.build_live_capabilities
    frames=[]
    start=time.monotonic()
    async def capture_factory(**kwargs):
        live=await factory(**kwargs)
        # Allow the sandbox postback to settle while recording; preserve verification.
        live.client.verification_timeout_ms = 45000
        if hasattr(live, "session"):
            recorder = ScreenRecorder(live.client.page, OUT/"screen")
            try:
                await asyncio.wait_for(recorder.start(),timeout=15)
            except BaseException:
                await live.close()
                raise
            live.session.screen_recorder = recorder
        execute=live.dispatcher.execute
        async def captured(call,state):
            result=await execute(call,state)
            # Capture real browser reads from the same continuous recording.
            url=str(result.get('url') or '')
            name = call.get('name') if isinstance(call, dict) else call.name
            if name=='read_page' and result.get('success'):
                path=OUT/f'portal-{len(frames):03}.png'
                try:
                    await live.client.page.screenshot(path=str(path.resolve()),timeout=10000)
                    frames.append({'path':str(path),'seconds':round(time.monotonic()-start,2),
                                   'action':name,'url':url,
                                   'flow':(result.get('data') or {}).get('flow')})
                    (OUT/'frames.json').write_text(json.dumps(frames,indent=2)+'\n')
                except Exception as exc:
                    print('Capture skipped:',type(exc).__name__,flush=True)
            return result
        live.dispatcher.execute=captured
        return live
    harness.build_live_capabilities=capture_factory
    try:
        return await harness.main([GOAL,'--attempts','1','--timeout','600',
                                   '--output',str(OUT/'run.json')])
    finally:
        (OUT/'frames.json').write_text(json.dumps(frames,indent=2)+'\n')

if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
