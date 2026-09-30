"""private loopback connection between the licet ui and the real sandbox agent"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = "https://licet.higgsfield.app"
LOCAL_ORIGIN = "http://127.0.0.1:8766"
ALLOWED_ORIGINS = frozenset((ORIGIN, LOCAL_ORIGIN))
PORT = 8765


class Bridge:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.process = None
        self.output = None
        self.goal = None

    def start(self, goal):
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
            raise ValueError("Enter a request of 1–2000 characters.")
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("A run is already active.")
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            output = ROOT / 'logs' / ('ui-live-' + stamp + '-' + secrets.token_hex(3))
            output.mkdir(parents=True, mode=0o700)
            env = dict(os.environ, LICET_CAPTURE_OUTPUT=str(output), LICET_CAPTURE_GOAL=goal.strip())
            with (output / 'console.txt').open('wb') as log:
                process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/phase9_capture_demo.py')],
                                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            self.goal, self.output, self.process = goal.strip(), output, process
            return self.status()

    def status(self):
        result = {'state': 'idle', 'mode': 'sandbox-plan-only', 'goal': self.goal,
                  'trace': [], 'report': None, 'has_frame': False,
                  'account_configured': all(dotenv_values(ROOT / '.env').get(k) or os.environ.get(k)
                      for k in ('ACCELA_TEST_USERNAME', 'ACCELA_TEST_PASSWORD', 'SOLARI_API_KEY')),
                  'authenticated': False}
        if not self.process:
            return result
        result['state'] = 'running' if self.process.poll() is None else 'finished'
        result['run_id'] = self.output.name
        trace = self.output / 'run.trace.jsonl'
        if trace.exists():
            for line in trace.read_text().splitlines():
                try:
                    result['trace'].append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        report = self.output / 'run.json'
        if report.exists():
            try:
                result['report'] = json.loads(report.read_text())
            except json.JSONDecodeError:
                pass
        result['has_frame'] = self.frame() is not None
        result['authenticated'] = result['has_frame']
        if result['state'] == 'finished' and not (result['report'] or {}).get('status'):
            result['state'] = 'error'
            result['error'] = 'Run stopped before producing a report. Inspect its private local log.'
            result['report'] = None
        return result

    def frame(self):
        if not self.output:
            return None
        # agent-created files only; no caller-supplied paths and no login captures
        paths = list((self.output / 'screen').glob('*.jpg')) + list(self.output.glob('portal-*.png'))
        return max(paths, key=lambda p: p.stat().st_mtime_ns) if paths else None


def handler_for(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # never log bearer tokens or user requests

        def allowed(self):
            return (self.headers.get('Host') == f'127.0.0.1:{PORT}'
                    and self.headers.get('Origin') in ALLOWED_ORIGINS)

        def reply(self, code, body, content_type='application/json'):
            data = json.dumps(body).encode() if content_type == 'application/json' else body
            self.send_response(code)
            if self.allowed():
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authenticated(self):
            if not self.allowed():
                self.reply(403, {'error': 'Origin or host not allowed.'})
                return False
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + bridge.token):
                self.reply(401, {'error': 'Connect using the local session key.'})
                return False
            return True

        def do_OPTIONS(self):
            if not self.allowed():
                return self.reply(403, {'error': 'Origin not allowed.'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()

        def do_GET(self):
            if not self.authenticated():
                return
            if self.path == '/status':
                return self.reply(200, bridge.status())
            if self.path == '/frame':
                path = bridge.frame()
                if path:
                    return self.reply(200, path.read_bytes(), 'image/png' if path.suffix == '.png' else 'image/jpeg')
                return self.reply(404, {'error': 'Browser has not connected yet.'})
            return self.reply(404, {'error': 'Not found.'})

        def do_POST(self):
            if not self.authenticated():
                return
            if self.path == '/booking-replay':
                # fixed offline harness only; no request parameters or live i/o
                from scripts.booking_replay_evidence import run_replay
                try:
                    return self.reply(200, run_replay())
                except Exception:
                    return self.reply(500, {'error': 'Offline replay failed; inspect the regression tests.'})
            if self.path != '/run':
                return self.reply(404, {'error': 'Not found.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Invalid request size.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict) or set(data) != {'goal'}:
                    raise ValueError('Only a goal may be supplied. Execution is plan-only.')
                return self.reply(202, bridge.start(data['goal']))
            except (ValueError, TypeError) as exc:
                return self.reply(400, {'error': str(exc)})
            except RuntimeError as exc:
                return self.reply(409, {'error': str(exc)})
    return Handler


def main():
    bridge = Bridge()
    directory = ROOT / 'logs/ui-bridge'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = directory / 'session-key.txt'
    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(bridge.token)
    # url fragments stay in the browser and are removed immediately by the ui
    pairing = directory / 'connect.html'
    fd = os.open(pairing, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('<!doctype html><meta charset="utf-8"><title>Connect Licet</title>'
                '<p>Connecting this browser to your local Licet agent...</p><script>'
                'location.replace(' + json.dumps(LOCAL_ORIGIN + '/#connect=' + bridge.token) + ');</script>')
    print(f'Licet connection listening on 127.0.0.1:{PORT}. Session key saved to {key}.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), handler_for(bridge)).serve_forever()


if __name__ == '__main__':
    main()
