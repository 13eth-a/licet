"""The recording observer must never change or abort a product tool result."""
import asyncio
from types import SimpleNamespace

import pytest

from licet.browser.dispatcher import ToolCall
from scripts import phase9_capture_demo as capture


@pytest.mark.parametrize('screenshot_fails',[False,True])
def test_capture_preserves_dict_and_typed_calls(monkeypatch,tmp_path,screenshot_fails):
    result={'success':True,'url':'https://aca-test.accela.com/Cap/CapDetail.aspx?IsToShowInspection=yes','data':{}}
    calls=[]
    async def execute(call,state):
        calls.append(call)
        return result
    async def screenshot(**kwargs):
        if screenshot_fails:
            raise RuntimeError('capture unavailable')
    live=SimpleNamespace(dispatcher=SimpleNamespace(execute=execute),client=SimpleNamespace(page=SimpleNamespace(screenshot=screenshot)))
    async def factory(**kwargs):return live
    async def harness_main(args):
        assert '--execute' not in args
        wrapped=await capture.harness.build_live_capabilities()
        for call in [{'name':'navigate','args':{}},{'name':'read_page','args':{}},ToolCall('read_page',{})]:
            assert await wrapped.dispatcher.execute(call,None) is result
        return 0
    monkeypatch.setattr(capture,'OUT',tmp_path)
    monkeypatch.setattr(capture.wiring,'SolariSession',capture.wiring.SolariSession)
    monkeypatch.setattr(capture.harness,'build_live_capabilities',factory)
    monkeypatch.setattr(capture.harness,'main',harness_main)
    assert asyncio.run(capture.main())==0
    assert len(calls)==3
