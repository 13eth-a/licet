"""the ui cannot bypass the local run boundary or expose files without pairing"""
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from scripts import licet_ui_bridge as ui


def request(bridge, *, origin=ui.ORIGIN, host='127.0.0.1:8765', token=None, path='/status', data=None):
    handler = object.__new__(ui.handler_for(bridge))
    raw = json.dumps(data).encode() if data is not None else b''
    handler.headers = {'Origin': origin, 'Host': host, 'Authorization': 'Bearer ' + (token or ''),
                       'Content-Length': str(len(raw))}
    handler.path, handler.rfile, handler.wfile = path, BytesIO(raw), BytesIO()
    response = []
    handler.reply = lambda *args: response.append(args)
    if data is None: handler.do_GET()
    else: handler.do_POST()
    return response[0]


def test_requires_token_and_exact_origin_and_host():
    bridge = ui.Bridge()
    assert request(bridge)[0] == 401
    assert request(bridge, token=bridge.token, origin='https://evil.example')[0] == 403
    assert request(bridge, token=bridge.token, host='evil.example:8765')[0] == 403
    assert request(bridge, token=bridge.token)[1]['state'] == 'idle'
    assert request(bridge, token=bridge.token, path='/../../.env')[0] == 404


def test_caller_cannot_enable_execution_or_choose_output_path():
    bridge = ui.Bridge()
    assert request(bridge, token=bridge.token, path='/run', data={'goal': 'Find permit', 'execute': True})[0] == 400
    assert request(bridge, token=bridge.token, path='/run', data={'goal': 'Find permit', 'output': '.env'})[0] == 400


def test_run_uses_real_capture_entrypoint_and_preserves_goal_without_shell(monkeypatch, tmp_path):
    bridge = ui.Bridge()
    monkeypatch.setattr(ui, 'ROOT', tmp_path)
    commands = []
    def spawn(args, **kwargs):
        commands.append((args, kwargs))
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(ui.subprocess, 'Popen', spawn)
    goal = 'Find permit 000000014; $(do-not-run)'
    state = bridge.start(goal)
    assert state['state'] == 'running'
    args, kwargs = commands[0]
    assert args == [ui.sys.executable, str(tmp_path / 'scripts/phase9_capture_demo.py')]
    assert kwargs['env']['LICET_CAPTURE_GOAL'] == goal
    assert 'shell' not in kwargs and '--execute' not in args
    with pytest.raises(RuntimeError, match='already active'): bridge.start(goal)


def test_failed_child_does_not_display_success_or_raw_log(tmp_path):
    bridge = ui.Bridge()
    bridge.process = SimpleNamespace(poll=lambda: 1)
    bridge.output = tmp_path
    (tmp_path/'console.txt').write_text('private authentication failure')
    state = bridge.status()
    assert state['state'] == 'error' and state['report'] is None
    assert 'private authentication' not in json.dumps(state)


def test_exception_report_is_not_a_successful_run(tmp_path):
    bridge = ui.Bridge()
    bridge.process = SimpleNamespace(poll=lambda: 0)
    bridge.output = tmp_path
    (tmp_path / 'run.json').write_text(json.dumps({'error': 'private exception details'}))
    state = bridge.status()
    assert state['state'] == 'error'
    assert state['report'] is None
    assert 'private exception' not in json.dumps(state)
