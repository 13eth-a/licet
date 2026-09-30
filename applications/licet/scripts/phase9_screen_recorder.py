"""Read-only continuous Chrome screencast; never sends browser input."""
import asyncio
import base64
import json
import time
from pathlib import Path


class ScreenRecorder:
    def __init__(self, page, output):
        self.page=page
        self.output=Path(output)
        self.frames=[]
        self.pending=set()
        self.started=time.monotonic()

    async def start(self):
        self.output.mkdir(parents=True,exist_ok=True)
        self.cdp=await self.page.context.new_cdp_session(self.page)
        self.cdp.on('Page.screencastFrame', self._receive)
        await self.cdp.send('Page.startScreencast',{'format':'jpeg','quality':75,'maxWidth':1440,'maxHeight':900,'everyNthFrame':6})

    def _receive(self,event):
        index=len(self.frames)
        path=self.output/f'{index:06}.jpg'
        path.write_bytes(base64.b64decode(event['data']))
        self.frames.append({'file':path.name,'seconds':time.monotonic()-self.started})
        task=asyncio.create_task(self.cdp.send('Page.screencastFrameAck',{'sessionId':event['sessionId']}))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def stop(self):
        await self.cdp.send('Page.stopScreencast')
        if self.pending:
            await asyncio.gather(*self.pending,return_exceptions=True)
        ended=time.monotonic()-self.started
        (self.output/'frames.json').write_text(json.dumps({'duration':ended,'frames':self.frames},indent=2)+'\n')
        await self.cdp.detach()
        if not self.frames:
            raise RuntimeError('No screen frames recorded')
