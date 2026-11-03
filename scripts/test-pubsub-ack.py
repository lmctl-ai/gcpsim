#!/usr/bin/env python3
"""REST ModifyAckDeadline: 0 redelivery, 10..600 bound, invalid ackId errors."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

exe = Path(sys.argv[1]).resolve()
cmd = [os.environ.get('JAVA', 'java'), '-Dfile.encoding=UTF-8', '-Dstdout.encoding=UTF-8',
       '-Dstderr.encoding=UTF-8', '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]
env = {k: v for k, v in os.environ.items() if not k.startswith(('SIM_AUTH', 'FLOCI_GCP_', 'QUARKUS_'))}
with tempfile.TemporaryDirectory(prefix='gcpsim-ack-') as tmp:
    tmp = Path(tmp)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'

    def call(path, body=None, method=None):
        req = urllib.request.Request(endpoint + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'},
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    with (tmp / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen(cmd + ['serve', '--port', str(port)], cwd=tmp, env=env,
                                  stdout=log, stderr=log)
        try:
            for _ in range(240):
                if server.poll() is not None: raise AssertionError('server exited early')
                try:
                    call('/health'); break
                except OSError:
                    time.sleep(.25)
            else:
                raise AssertionError('readiness timeout')
            assert call('/v1/projects/gcpsim-local/topics/ack-topic', {}, 'PUT')[0] == 200
            assert call('/v1/projects/gcpsim-local/subscriptions/ack-sub',
                        {'topic': 'projects/gcpsim-local/topics/ack-topic',
                         'ackDeadlineSeconds': 30, 'expirationPolicy': {}}, 'PUT')[0] == 200
            assert call('/v1/projects/gcpsim-local/topics/ack-topic:publish',
                        {'messages': [{'data': 'b25l'}]}, 'POST')[0] == 200
            _, pulled = call('/v1/projects/gcpsim-local/subscriptions/ack-sub:pull',
                             {'maxMessages': 1, 'returnImmediately': False}, 'POST')
            ack = pulled['receivedMessages'][0]['ackId']
            base = '/v1/projects/gcpsim-local/subscriptions/ack-sub:modifyAckDeadline'
            assert call(base, {'ackIds': [ack], 'ackDeadlineSeconds': 30}, 'POST')[0] == 200
            _, hidden = call('/v1/projects/gcpsim-local/subscriptions/ack-sub:pull',
                             {'maxMessages': 1, 'returnImmediately': False}, 'POST')
            assert hidden['receivedMessages'] == [], hidden
            assert call(base, {'ackIds': [ack], 'ackDeadlineSeconds': 0}, 'POST')[0] == 200
            _, redelivered = call('/v1/projects/gcpsim-local/subscriptions/ack-sub:pull',
                                  {'maxMessages': 1, 'returnImmediately': False}, 'POST')
            assert len(redelivered['receivedMessages']) == 1, redelivered
            ack2 = redelivered['receivedMessages'][0]['ackId']
            assert call('/v1/projects/gcpsim-local/subscriptions/ack-sub:acknowledge',
                        {'ackIds': [ack2]}, 'POST')[0] == 200
            for body in ({'ackIds': [''], 'ackDeadlineSeconds': 10},
                         {'ackIds': [ack2], 'ackDeadlineSeconds': 601},
                         {'ackIds': [ack2], 'ackDeadlineSeconds': 0.5},
                         {'ackIds': [ack2], 'ackDeadlineSeconds': 4294967296},
                         {'ackIds': [], 'ackDeadlineSeconds': 10}):
                status, err = call(base, body, 'POST')
                assert status == 400 and err['error']['status'] == 'INVALID_ARGUMENT', (status, err)
            # Stale/already-acked well-formed ids are accepted like upstream acknowledge.
            assert call(base, {'ackIds': [ack2], 'ackDeadlineSeconds': 10}, 'POST')[0] == 200
            # Mixed valid + unknown well-formed (UUID) ids is accepted; unknown ids are ignored.
            assert call(base, {'ackIds': [ack2, '11111111-2222-3333-4444-555555555555'], 'ackDeadlineSeconds': 10}, 'POST')[0] == 200
            # Malformed ids (not issued by this simulator) are rejected, and nothing is applied.
            status, err = call(base, {'ackIds': [ack2, 'bogus'], 'ackDeadlineSeconds': 10}, 'POST')
            assert status == 400 and err['error']['status'] == 'INVALID_ARGUMENT', (status, err)
            print('PASS: modifyAckDeadline 0 redelivery, 30s hide, bounds and invalid ackId errors')
        except BaseException:
            log.flush()
            print((tmp / 'server.log').read_text(encoding='utf-8'), file=sys.stderr)
            raise
        finally:
            server.terminate()
            try: server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=10)
