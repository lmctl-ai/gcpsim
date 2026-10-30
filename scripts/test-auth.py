#!/usr/bin/env python3
"""Real HTTP auth matrix and optional pinned lmgcp binary integration. Owns all processes."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
exe = Path(sys.argv[1]).resolve()
client = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
cmd = [os.environ.get('JAVA', 'java'), '-Dfile.encoding=UTF-8', '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]
env = {k: v for k, v in os.environ.items() if not k.startswith(('SIM_AUTH', 'FLOCI_GCP_', 'QUARKUS_', 'LMGCP_', 'GOOGLE_', 'CLOUDSDK_'))}
TOKEN = 'dummy-private-token'
PRINCIPAL = 'user:tester@example.com'
allow = {'Effect': 'Allow', 'ActionGlob': '*', 'ResourceGlob': '*', 'Principal': PRINCIPAL}
with tempfile.TemporaryDirectory(prefix='gcpsim-auth-') as directory:
    tmp = Path(directory)
    for invalid in ({}, {'tokens': {TOKEN: PRINCIPAL}, 'Statement': [{'Effect': 'Permit'}]},
                    {'tokens': {TOKEN: PRINCIPAL}, 'policies': {'buckets/x': {'bindings': [{'role': 'roles/storage.admin', 'members': [PRINCIPAL], 'condition': {}}]}}}):
        bad = tmp / 'invalid.json'
        bad.write_text(json.dumps(invalid), encoding='utf-8')
        result = subprocess.run(cmd + ['serve', '--auth', 'enforce', '--auth-config', str(bad)], env=env, cwd=tmp, capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert result.returncode == 2 and TOKEN not in result.stdout + result.stderr
    print('PASS: malformed auth fixtures fail startup without leaking tokens')

    def run(config, check, flags=True):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        endpoint = f'http://127.0.0.1:{port}'
        config_path = tmp / 'auth.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        server_env = dict(env)
        args = ['serve', '--port', str(port)]
        if flags:
            args += ['--auth', 'enforce', '--auth-config', str(config_path)]
        else:
            server_env.update(SIM_AUTH='enforce', SIM_AUTH_CONFIG=str(config_path))
        def request(path, method='GET', data=None, token=TOKEN, status=200, content_type='application/json'):
            headers = {'Content-Type': content_type}
            if token is not None:
                headers['Authorization'] = token if token.startswith('Basic ') else 'Bearer ' + token
            req = urllib.request.Request(endpoint + path, data=json.dumps(data).encode() if data is not None else None, headers=headers, method=method)
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read()
                assert response.status == status, (path, status, response.status, raw)
                if status in (401, 403):
                    body = json.loads(raw)['error']
                    assert body['code'] == status and body['status'] == ('UNAUTHENTICATED' if status == 401 else 'PERMISSION_DENIED'), body
                    assert TOKEN.encode() not in raw
                return json.loads(raw) if raw else None
        def cli(fixture, token=TOKEN, error=None):
            ce = dict(env, LMGCP_ENDPOINT=endpoint, LMGCP_PROJECT='gcpsim-local')
            if token is not None:
                ce['LMGCP_ACCESS_TOKEN'] = token
            result = subprocess.run([str(client), '--dsl', str(ROOT / 'tests/fixtures' / fixture)], cwd=tmp, env=ce, capture_output=True, text=True, encoding='utf-8', timeout=90)
            combined = result.stdout + result.stderr
            assert TOKEN not in combined and 'wrong-private-token' not in combined, combined
            if error:
                assert result.returncode != 0 and str(error) in combined, (result.returncode, combined)
            else:
                assert result.returncode == 0, combined
        with (tmp / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen(cmd + args, env=server_env, cwd=tmp, stdout=log, stderr=log)
            try:
                for _ in range(240):
                    if server.poll() is not None:
                        raise AssertionError('server exited before readiness')
                    try:
                        request('/health', token=None)
                        break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise AssertionError('readiness timeout')
                check(request, cli)
            except BaseException:
                log.flush()
                print((tmp / 'server.log').read_text(encoding='utf-8'), file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
        assert TOKEN not in (tmp / 'server.log').read_text(encoding='utf-8')

    def matrix(req, cli):
        for token in (None, 'wrong-private-token', '', 'Basic invalid', 'space token'):
            req('/storage/v1/b?project=gcpsim-local', token=token, status=401)
        req('/storage/v1/b?project=gcpsim-local', 'POST', {'name': 'allowed-bucket'})
        req('/storage/v1/b/allowed-bucket')
        req('/storage/v1/b/allowed-bucket', 'DELETE', status=403)
        req('/v1/projects/gcpsim-local/topics/allowed-topic', 'PUT', {})
        req('/v1/projects/gcpsim-local/topics/allowed-topic:publish', 'POST', {'messages': [{'data': 'aGk='}]})
        req('/v1/projects/gcpsim-local/topics/allowed-topic', 'DELETE', status=403)
        req('/storage/v1/b/allowed-bucket/iam', status=403)
        req('/google.pubsub.v1.Publisher/ListTopics', 'POST', {}, status=403, content_type='application/grpc')
        req('/google.pubsub.v1.Publisher/ListTopics', 'POST', {}, token=None, status=401, content_type='application/grpc')
        req('/compute/v1/projects/gcpsim-local', status=403)
        print('PASS: missing/wrong/malformed credentials; allow; explicit deny wins; unsupported transports fail closed')
        if client:
            cli('roundtrip.dslb')
            # Independent simulator reads prove the client did not use its internal fake.
            req('/storage/v1/b/lmgcp-integration/o/hello.txt')
            req('/v1/projects/gcpsim-local/topics/lmgcp-integration')
            cli('denied.dslb', token=None, error=401)
            cli('denied.dslb', token='wrong-private-token', error=401)
            print('PASS: lmgcp binary -> GCS upload/download + Pub/Sub publish/pull; CLI 401 exit/no token leakage')
    run({'tokens': {TOKEN: PRINCIPAL}, 'Statement': [
        {'Effect': 'Deny', 'ActionGlob': '*.delete', 'ResourceGlob': '*'}, allow]}, matrix)

    def bindings(req, cli):
        # Precedence: bucket/topic bindings grant read, wildcard statements bootstrap creation.
        req('/storage/v1/b?project=gcpsim-local', 'POST', {'name': 'fixture-bucket'})
        req('/storage/v1/b/fixture-bucket')
        req('/storage/v1/b/fixture-bucket', 'DELETE', status=403)
        req('/v1/projects/gcpsim-local/topics/fixture-topic', 'PUT', {})
        req('/v1/projects/gcpsim-local/topics/fixture-topic:publish', 'POST', {'messages': [{'data': 'aGk='}]})
        req('/v1/projects/gcpsim-local/topics/fixture-topic', status=403)
        req('/storage/v1/b/other-bucket', status=403)
        if client:
            cli('denied.dslb', error=403)
        print('PASS: upstream bucket bindings, Pub/Sub publisher bindings, wildcard action/resource, default deny, client 403')
    run({'tokens': {TOKEN: PRINCIPAL}, 'Statement': [
        {'Effect': 'Allow', 'ActionGlob': '*.create', 'ResourceGlob': 'projects/gcpsim-*'},
        {'Effect': 'Deny', 'Action': 'storage.buckets.delete', 'ResourceGlob': '*'}],
        'policies': {'buckets/fixture-bucket': {'bindings': [{'role': 'roles/storage.admin', 'members': [PRINCIPAL]}]},
                     'projects/gcpsim-local/topics/fixture-topic': {'bindings': [{'role': 'roles/pubsub.publisher', 'members': [PRINCIPAL]}]}},
    }, bindings, flags=False)

    run({'tokens': {TOKEN: PRINCIPAL}, 'Statement': []}, lambda req, cli: req('/storage/v1/b?project=gcpsim-local', status=403))
    run({'tokens': {TOKEN: PRINCIPAL}}, lambda req, cli: req('/storage/v1/b?project=gcpsim-local'))
    print('PASS: empty policies default deny; absent policies allow authenticated mapped operations')
