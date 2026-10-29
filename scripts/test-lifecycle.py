#!/usr/bin/env python3
"""Persistence, start alias, occupied-port failure and process ownership."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

exe = Path(sys.argv[1]).resolve()
cmd = [os.environ.get('JAVA', 'java'), '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]
env = {k: v for k, v in os.environ.items() if not k.startswith(('FLOCI_GCP_', 'QUARKUS_'))}
with tempfile.TemporaryDirectory(prefix='gcpsim-lifecycle-') as tmp:
    tmp = Path(tmp)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    def request(path, body=None):
        req = urllib.request.Request(endpoint + path, data=json.dumps(body).encode() if body else None,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)

    for iteration in range(2):
        with (tmp / 'server.log').open('w') as log:
            server = subprocess.Popen(cmd + ['start', '--port', str(port), '--data-dir', str(tmp / 'state')],
                                      cwd=tmp, env=env, stdout=log, stderr=log)
            try:
                for _ in range(240):
                    if server.poll() is not None:
                        raise AssertionError('server failed before readiness')
                    try:
                        request('/health')
                        break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise AssertionError('server readiness timeout')
                if iteration == 0:
                    request('/storage/v1/b?project=gcpsim-local', {'name': 'gcpsim-durable'})
                    collision = subprocess.run(cmd + ['serve', '--port', str(port)], env=env, cwd=tmp,
                                               capture_output=True, text=True, timeout=60)
                    assert collision.returncode == 3, (collision.returncode, collision.stdout, collision.stderr)
                    assert json.loads(collision.stdout)['ok'] is False, collision.stdout
                    print('PASS: occupied port fails with exit 3 and JSON error')
                assert request('/storage/v1/b/gcpsim-durable')['name'] == 'gcpsim-durable'
            except BaseException:
                log.flush()
                print((tmp / 'server.log').read_text(), file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
    print('PASS: start alias and persistent bucket survives process restart')
