#!/usr/bin/env python3
"""Real gcloud GCS + Pub/Sub round trips against an owned gcpsim process.
No cloud credentials, Docker, or runtime internet needed. gcloud must be installed.
"""
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
import urllib.request
import uuid

exe = Path(sys.argv[1]).resolve()
command = [os.environ.get('JAVA', 'java'), '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]
gcloud = shutil.which(os.environ.get('GCLOUD', 'gcloud'))
if not gcloud:
    raise SystemExit('gcloud is required (tested with 543.0.0); no silent skip')
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
endpoint = f'http://127.0.0.1:{port}'
with tempfile.TemporaryDirectory(prefix='gcpsim-test-') as tmp:
    tmp = Path(tmp)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLOUDSDK_', 'GOOGLE_', 'FLOCI_GCP_', 'QUARKUS_'))}
    env.update({
        'CLOUDSDK_CONFIG': str(tmp / 'gcloud'),
        'CLOUDSDK_CORE_PROJECT': 'gcpsim-local',
        'CLOUDSDK_AUTH_ACCESS_TOKEN': 'gcpsim-local-token',
        'CLOUDSDK_CORE_DISABLE_PROMPTS': '1',
        'CLOUDSDK_CORE_DISABLE_USAGE_REPORTING': 'true',
        'CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK': 'true',
        'CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API': 'false',
        'CLOUDSDK_API_ENDPOINT_OVERRIDES_STORAGE': endpoint + '/storage/v1/',
        'CLOUDSDK_API_ENDPOINT_OVERRIDES_PUBSUB': endpoint + '/',
        'CLOUDSDK_STORAGE_PROCESS_COUNT': '1',
        'CLOUDSDK_STORAGE_THREAD_COUNT': '1',
        'NO_PROXY': '127.0.0.1,localhost',
    })
    def cloud(*args, json_output=True):
        cmd = [gcloud, '--quiet'] + list(args)
        if json_output:
            cmd += ['--format=json']
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
        return json.loads(r.stdout) if json_output and r.stdout.strip() else r.stdout

    log_path = tmp / 'server.log'
    with log_path.open('w') as log:
        server = subprocess.Popen(command + ['serve', '--port', str(port)], cwd=tmp,
                                  env=env, stdout=log, stderr=log)
        try:
            for _ in range(240):
                if server.poll() is not None:
                    raise AssertionError('emulator exited before readiness')
                try:
                    with urllib.request.urlopen(endpoint + '/health', timeout=1) as r:
                        if r.status == 200:
                            break
                except OSError:
                    time.sleep(0.25)
            else:
                raise AssertionError('emulator readiness timeout')
            token = uuid.uuid4().hex[:12]
            bucket, topic, sub = ('gcpsim-' + token + s for s in ('-bucket', '-topic', '-sub'))
            cloud('storage', 'buckets', 'create', 'gs://' + bucket, '--location=us-central1')
            original = tmp / 'payload.txt'
            payload = b'gcpsim real gcloud round trip\n\x00\xff\n'
            original.write_bytes(payload)
            cloud('storage', 'cp', str(original), f'gs://{bucket}/payload.bin', json_output=False)
            downloaded = tmp / 'download.bin'
            cloud('storage', 'cp', f'gs://{bucket}/payload.bin', str(downloaded), json_output=False)
            assert downloaded.read_bytes() == payload, 'GCS bytes differ'
            print('PASS: GCS bucket create, upload and byte-exact download')
            cloud('pubsub', 'topics', 'create', topic)
            cloud('pubsub', 'subscriptions', 'create', sub, '--topic=' + topic)
            published = cloud('pubsub', 'topics', 'publish', topic, '--message=hello-gcpsim')
            assert published['messageIds'], published
            received = []
            for _ in range(10):
                received = cloud('pubsub', 'subscriptions', 'pull', sub, '--auto-ack', '--limit=1')
                if received:
                    break
                time.sleep(0.2)
            assert received, 'Pub/Sub received no messages'
            data = received[0]['message']['data']
            assert data == 'hello-gcpsim' or base64.b64decode(data) == b'hello-gcpsim', received
            print('PASS: Pub/Sub topic, subscription, publish, pull and acknowledgement')
            cloud('pubsub', 'subscriptions', 'delete', sub)
            cloud('pubsub', 'topics', 'delete', topic)
            cloud('storage', 'rm', f'gs://{bucket}/payload.bin', json_output=False)
            cloud('storage', 'buckets', 'delete', 'gs://' + bucket)
            print('PASS: resource cleanup')
        except BaseException:
            log.flush()
            print(log_path.read_text(), file=sys.stderr)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
print('PASS: emulator process stopped')
