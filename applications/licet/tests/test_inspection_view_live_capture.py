"""p13's captured summary/inspection views through real dispatch and policy"""
import asyncio
import json
from pathlib import Path

import pytest

from licet.browser.accela import parse_ref_from_url
from licet.browser.dispatcher import ToolDispatcher
from licet.browser.errors import BrowserError, ToolError
from licet.browser.solari_client import ToolResult
from licet.phase3.runner import Phase3RetrievalRunner
from licet.phase3.state import PermitState, CoverageStatus
from licet.phase4.accela_portal import AccelaInspectionPortal
from tests.test_phase3_integration import RetrievalFakeClient

PAGES = json.loads((Path(__file__).parent / 'fixtures/ni_inspection_views.json').read_text())['pages']
KEY = 'NULLISLAND/Building/REC26/00000/000QB'


class CapturedViewClient(RetrievalFakeClient):
    def __init__(self, *, loading=False, foreign=False, blocked=False):
        super().__init__(PAGES[0]['url'], PAGES)
        self.loading, self.foreign, self.blocked = loading, foreign, blocked

    async def read_page(self, **kwargs):
        self.calls.append(('read_page', kwargs))
        data = dict(PAGES[1] if self.url.endswith('IsToShowInspection=yes') else PAGES[0])
        if self.loading:
            data['loading'] = ['Loading...']
        if self.foreign and self.url.endswith('yes'):
            data['url'] = data['url'].replace('000QB', '000XX')
            data['text'] = data['text'].replace('000000014', '000000099')
        return ToolResult(ok=True, url=data['url'], data=data, error=None)

    async def click(self, target):
        self.calls.append(('click', {'target':target.describe()}))
        return ToolResult(ok=False, url=self.url, data={}, error=ToolError(
            BrowserError.NOT_FOUND if 'History' in target.describe() else BrowserError.NOT_ACTIONABLE,
            'section label unavailable'))

    async def wait_for_text(self, **kwargs):
        return ToolResult(ok=True, url=self.url, data={}, error=None)


def test_retrieval_reads_captured_inspection_view_without_wizard_or_submission():
    client = CapturedViewClient()
    runner = Phase3RetrievalRunner(ToolDispatcher(client), record_ref=parse_ref_from_url(client.url))
    runner._current_url = client.url
    state = PermitState(record_key=KEY)
    result = asyncio.run(runner.retrieve_missing_sections(state,[{'section':'inspections'}]))
    assert result.sections_retrieved == ['inspections']
    assert state.coverage['inspections'].status == CoverageStatus.EXPLICITLY_EMPTY
    navigations = [args['url'] for name,args in client.calls if name=='navigate']
    assert navigations == [PAGES[1]['url']]
    assert [args['target'] for name,args in client.calls if name=='click'] == ['text=Inspections','text=Inspection History']


@pytest.mark.parametrize('loading,foreign',[(True,False),(False,True)])
def test_retrieval_never_accepts_loading_or_foreign_view(loading,foreign):
    client = CapturedViewClient(loading=loading,foreign=foreign)
    runner = Phase3RetrievalRunner(ToolDispatcher(client), record_ref=parse_ref_from_url(client.url))
    runner._current_url=client.url
    state=PermitState(record_key=KEY)
    result=asyncio.run(runner.retrieve_missing_sections(state,[{'section':'inspections'}]))
    assert result.sections_failed or state.rejected_observations
    assert state.record_key==KEY


@pytest.mark.parametrize('loading,foreign',[(False,False),(True,False),(False,True)])
def test_executor_reader_uses_same_view_and_rechecks_identity(loading,foreign):
    client=CapturedViewClient(loading=loading,foreign=foreign)
    portal=AccelaInspectionPortal(ToolDispatcher(client),record_ref=parse_ref_from_url(client.url))
    result=portal.read_inspection_state('000000014','Brycer Inspection History')
    if loading or foreign:
        assert result.status.startswith('Unknown')
    else:
        assert result.status=='Not Scheduled'
        assert result.record_key==KEY


@pytest.mark.parametrize('reader',['reasoning','executor'])
def test_guard_block_never_triggers_an_alternate_route(reader):
    client=CapturedViewClient()
    dispatcher=ToolDispatcher(client)
    original=dispatcher.execute
    async def execute(call,state):
        if call.name=='click':
            return {'success':False,'blocked':True,'error':{'kind':'not_actionable','message':'present but not visible'}}
        return await original(call,state)
    dispatcher.execute=execute
    ref=parse_ref_from_url(client.url)
    if reader=='reasoning':
        runner=Phase3RetrievalRunner(dispatcher,record_ref=ref)
        runner._current_url=client.url
        result=asyncio.run(runner.retrieve_missing_sections(PermitState(record_key=KEY),[{'section':'inspections'}]))
        assert result.sections_failed
    else:
        result=AccelaInspectionPortal(dispatcher,record_ref=ref).read_inspection_state('000000014','Brycer Inspection History')
        assert result.status.startswith('Unknown')
    assert not any(name=='navigate' for name,_ in client.calls)
