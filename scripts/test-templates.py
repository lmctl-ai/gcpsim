#!/usr/bin/env python3
"""Current-engine JSON templates -> pinned v5 client -> real simulator, both auth modes."""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
exe, client = (Path(arg).resolve() for arg in sys.argv[1:3])
engine = os.environ.get('LMSCRIPT_BIN', shutil.which('lmscript'))
compiler = os.environ.get('LMGCP_COMPILER')
assert engine and compiler, 'Set LMSCRIPT_BIN (current engine) and LMGCP_COMPILER (isolated 0.2.420)'
cmd = [os.environ.get('JAVA', 'java'), '-Dfile.encoding=UTF-8', '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]
env = {k: v for k, v in os.environ.items() if not k.startswith(('SIM_AUTH', 'FLOCI_GCP_', 'QUARKUS_', 'LMGCP_', 'GOOGLE_', 'CLOUDSDK_'))}
env.update(PATH=str(client.parent) + os.pathsep + env['PATH'], LMGCP_COMPILER=compiler,
           LMGCP_TEMPLATE_CLIENT=str(ROOT / 'examples/templates-client.lms'), LMGCP_PROJECT='gcpsim-local')
TOKEN = 'dummy-template-token'
with tempfile.TemporaryDirectory(prefix='gcpsim-templates-') as directory:
    tmp = Path(directory)

    def script(stage, run_env):
        # Never accept a stale compiled artifact after a failed compilation.
        (tmp / 'templates.dslb').unlink(missing_ok=True)
        result = subprocess.run([shutil.which("node"), engine, str(ROOT / 'examples/templates.lms'), stage], cwd=tmp,
                                env=run_env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0 and f'PASS templates {stage}' in result.stdout, result.stdout + result.stderr
        assert TOKEN not in result.stdout + result.stderr

    script('prepare', env)
    expected = json.loads((tmp / 'expected.json').read_text(encoding='utf-8'))
    obj, pubsub = expected['object'], expected['pubsub']
    assert obj['content']['count'] == 3 and obj['content']['tags'] == ['雪', 'quoted']
    assert obj['content']['note'] == 'quoted "雪" and \\ paths; literal ${untouched}'
    assert obj['content']['description'] == 'created by ' + obj['content']['note'] + ' pipeline'
    config = json.loads((tmp / 'auth.json').read_text(encoding='utf-8'))
    assert config['Statement'][2]['ResourceGlob'] == 'projects/_/buckets/lmgcp-templates'

    for mode in ('open', 'enforce'):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        endpoint = f'http://127.0.0.1:{port}'
        run_env = dict(env, LMGCP_ENDPOINT=endpoint)
        if mode == 'enforce':
            run_env['LMGCP_ACCESS_TOKEN'] = TOKEN
        args = ['serve', '--port', str(port), '--auth', mode]
        if mode == 'enforce':
            args += ['--auth-config', str(tmp / 'auth.json')]

        def request(path, method='GET', data=None, status=200, token=TOKEN, raw=False):
            headers = {'Content-Type': 'application/json'}
            if token is not None:
                headers['Authorization'] = 'Bearer ' + token
            req = urllib.request.Request(endpoint + path, method=method, headers=headers,
                                         data=json.dumps(data).encode() if data is not None else None)
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read()
                assert response.status == status, (path, response.status, body)
                value = body if raw else json.loads(body) if body else None
                if status in (401, 403):
                    assert value['error']['code'] == status
                    assert value['error']['status'] == ('UNAUTHENTICATED' if status == 401 else 'PERMISSION_DENIED')
                    assert isinstance(value['error']['message'], str) and TOKEN.encode() not in body
                return value

        with (tmp / f'{mode}.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen(cmd + args, env=env, cwd=tmp, stdout=log, stderr=log)
            try:
                for _ in range(240):
                    assert server.poll() is None, 'server exited before readiness'
                    try:
                        request('/health', token=None)
                        break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise AssertionError('readiness timeout')
                script('run', run_env)
                bucket = request('/storage/v1/b/lmgcp-templates')
                assert bucket['name'] == expected['create']['name']
                assert bucket['labels']['wave'] == expected['create']['labels']['wave']
                metadata = request('/storage/v1/b/lmgcp-templates/o/template.json')
                assert metadata['name'] == obj['name'] and metadata['contentType'] == obj['contentType']
                content = request('/storage/v1/b/lmgcp-templates/o/template.json?alt=media', raw=True)
                assert json.loads(content) == obj['content']
                topic = request('/v1/projects/gcpsim-local/topics/lmgcp-templates')
                assert topic['name'] == 'projects/gcpsim-local/topics/' + pubsub['topic']
                messages = request('/v1/projects/gcpsim-local/subscriptions/lmgcp-template-sub:pull', 'POST', {'maxMessages': 1})
                message = messages['receivedMessages'][0]['message']
                assert base64.b64decode(message['data']).decode() == pubsub['message']
                assert message['attributes'] == pubsub['attributes']
                if mode == 'enforce':
                    request('/storage/v1/b/lmgcp-templates', token=None, status=401)
                    request('/storage/v1/b/lmgcp-templates', 'DELETE', status=403)
                    request('/storage/v1/b/lmgcp-denied', status=403)
                    script('denied', run_env)
                print(f'PASS templates {mode}: client fields + independent HTTP statuses, object bytes, Pub/Sub attributes' + ('; IAM allow/deny and vendor errors' if mode == 'enforce' else ''))
            except BaseException:
                log.flush()
                print((tmp / f'{mode}.log').read_text(encoding='utf-8'), file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
