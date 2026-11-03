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
if client is not None:
    help_out = subprocess.run([str(client), '--help'], capture_output=True, text=True, encoding='utf-8').stdout
    if '--lmcode' not in help_out:
        print('SKIP: pinned lmgcp client lacks --lmcode (only --dsl); client integration skipped')
        client = None
cmd = [os.environ.get('JAVA', 'java'), '-Dfile.encoding=UTF-8', '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]
env = {k: v for k, v in os.environ.items() if not k.startswith(('SIM_AUTH', 'FLOCI_GCP_', 'QUARKUS_', 'LMGCP_', 'GOOGLE_', 'CLOUDSDK_'))}
TOKEN = 'dummy-private-token'
PRINCIPAL = 'user:tester@example.com'
allow = {'Effect': 'Allow', 'ActionGlob': '*', 'ResourceGlob': 'projects/*', 'Principal': PRINCIPAL}
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
            req = urllib.request.Request(endpoint + path, data=data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None, headers=headers, method=method)
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
            result = subprocess.run([str(client), '--lmcode', str(ROOT / 'tests/fixtures' / fixture)], cwd=tmp, env=ce, capture_output=True, text=True, encoding='utf-8', timeout=90)
            combined = result.stdout + result.stderr
            assert TOKEN not in combined and 'wrong-private-token' not in combined, combined
            if error:
                assert result.returncode == 4 and str(error) in combined, (result.returncode, combined)
                assert json.loads(result.stdout)['code'] == 'APP_FAIL', result.stdout
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
            req('/v1/projects/gcpsim-local/topics', token=token, status=401)
        req('/storage/v1/b?project=gcpsim-local', token='other-dummy-token', status=403)
        req('/storage/v1/b?project=gcpsim-local', 'POST', {'name': 'allowed-bucket'})
        req('/storage/v1/b/allowed-bucket')
        req('/upload/storage/v1/b/allowed-bucket/o?uploadType=media&name=blocked%0Aobject', 'POST', b'x', status=403, content_type='text/plain')
        multipart = b'--test-boundary\r\nContent-Type: text/plain\r\n\r\n{"name":"blocked-object"}\r\n--test-boundary\r\nContent-Type: application/json\r\n\r\n{"name":"allowed-object"}\r\n--test-boundary--\r\n'
        req('/upload/storage/v1/b/allowed-bucket/o?uploadType=multipart', 'POST', multipart, status=403, content_type='multipart/related; boundary=test-boundary')
        fallback = b'--test-boundary\r\nContent-Type: application/json\r\n\r\n{"name":null}\r\n--test-boundary\r\nContent-Type: text/plain\r\n\r\npayload\r\n--test-boundary--\r\n'
        req('/upload/storage/v1/b/allowed-bucket/o?uploadType=multipart&name=blocked-null', 'POST', fallback, status=403, content_type='multipart/related; boundary=test-boundary')
        req('/storage/v1/b/allowed-bucket', 'DELETE', status=403)
        req('/storage/v1/b/allowed-bucket')
        req('/v1/projects/gcpsim-local/topics/allowed-topic', 'PUT', {})
        req('/v1/projects/gcpsim-local/topics/allowed-topic:publish', 'POST', {'messages': [{'data': 'aGk='}]})
        req('/v1/projects/gcpsim-local/topics/allowed-topic', 'DELETE', status=403)
        req('/storage/v1/b/allowed-bucket/iam', status=403)
        req('/google.pubsub.v1.Publisher/ListTopics', 'POST', {}, status=403, content_type='application/grpc')
        req('/google.pubsub.v1.Publisher/ListTopics', 'POST', {}, token=None, status=401, content_type='application/grpc')
        req('/google.firestore.v1.Firestore/GetDocument', 'POST', {}, token=None, status=401, content_type='application/grpc')
        req('/google.firestore.v1.Firestore/GetDocument', 'POST', {}, token='other-dummy-token', status=403, content_type='application/grpc')
        req('/google.firestore.v1.Firestore/DeleteDocument', 'POST', {}, status=403, content_type='application/grpc')
        req('/google.firestore.v1.Firestore/UnknownMethod', 'POST', {}, status=403, content_type='application/grpc')
        req('/compute/v1/projects/gcpsim-local', status=403)
        print('PASS: credentials; allow/deny; newline and multipart resource identity; unsupported transports fail closed')
        if client:
            cli('roundtrip.dslb')
            # Independent simulator reads prove the client did not use its internal fake.
            req('/storage/v1/b/lmgcp-integration/o/hello.txt')
            req('/v1/projects/gcpsim-local/topics/lmgcp-integration')
            cli('denied.dslb', token=None, error=401)
            cli('denied.dslb', token='wrong-private-token', error=401)
            print('PASS: lmgcp binary -> GCS upload/download + Pub/Sub publish/pull; CLI 401 exit/no token leakage')
    run({'tokens': {TOKEN: PRINCIPAL, 'other-dummy-token': 'user:other@example.com'}, 'Statement': [
        {'Effect': 'Deny', 'ActionGlob': '*.delete', 'ResourceGlob': '*'},
        {'Effect': 'Deny', 'Action': 'storage.objects.create', 'ResourceGlob': '*blocked*'}, allow],
        'policies': {'buckets/allowed-bucket': {'bindings': [{'role': 'roles/storage.objectAdmin', 'members': [PRINCIPAL]}]}}}, matrix)

    def bindings(req, cli):
        # Precedence: bucket/topic bindings grant read, wildcard statements bootstrap creation.
        req('/storage/v1/b?project=gcpsim-local', 'POST', {'name': 'fixture-bucket'})
        req('/storage/v1/b/fixture-bucket')
        req('/storage/v1/b/fixture-bucket', 'DELETE', status=403)
        req('/v1/projects/gcpsim-local/topics/fixture-topic', 'PUT', {})
        req('/v1/projects/gcpsim-local/topics/fixture-topic:publish', 'POST', {'messages': [{'data': 'aGk='}]})
        req('/v1/projects/gcpsim-local/topics/fixture-topic', status=403)
        req('/storage/v1/b/other-bucket', status=403)
        req('/v1/projects/other-project/topics/fixture-topic', 'PUT', {}, status=403)
        if client:
            cli('denied.dslb', error=403)
        print('PASS: upstream bucket bindings, Pub/Sub publisher bindings, wildcard action/resource, default deny, client 403')
    run({'tokens': {TOKEN: PRINCIPAL}, 'Statement': [
        {'Effect': 'Allow', 'ActionGlob': '*.create', 'ResourceGlob': 'projects/gcpsim-local'},
        {'Effect': 'Deny', 'Action': 'storage.buckets.delete', 'ResourceGlob': '*'}],
        'policies': {'buckets/fixture-bucket': {'bindings': [{'role': 'roles/storage.admin', 'members': [PRINCIPAL]}]},
                     'projects/gcpsim-local/topics/fixture-topic': {'bindings': [{'role': 'roles/pubsub.publisher', 'members': [PRINCIPAL]}]}},
    }, bindings, flags=False)

    run({'tokens': {TOKEN: PRINCIPAL}, 'Statement': []}, lambda req, cli: req('/storage/v1/b?project=gcpsim-local', status=403))
    run({'tokens': {TOKEN: PRINCIPAL}}, lambda req, cli: req('/storage/v1/b?project=gcpsim-local'))
    print('PASS: empty policies default deny; absent policies allow authenticated mapped operations')

    if exe.suffix == '.jar' and (exe.parent / 'app').is_dir():
        probe = tmp / 'io/floci/gcp/services/firestore/CheckFirestorePagination.java'
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("""package io.floci.gcp.services.firestore;
import com.google.firestore.v1.*;
import io.floci.gcp.core.storage.InMemoryStorage;
import io.floci.gcp.services.firestore.model.StoredDocument;
import io.grpc.stub.StreamObserver;
import java.time.Instant;
import java.util.*;
public class CheckFirestorePagination {
    static class Obs implements StreamObserver<ListDocumentsResponse> {
        ListDocumentsResponse resp;
        public void onNext(ListDocumentsResponse v) { resp = v; }
        public void onError(Throwable t) { throw new RuntimeException(t); }
        public void onCompleted() {}
    }
    public static void main(String[] args) {
        String parent = "projects/p1/databases/(default)/documents";
        FirestoreService svc = new FirestoreService(new InMemoryStorage<String, StoredDocument>());
        FirestoreController ctl = new FirestoreController(svc);
        for (String id : List.of("doc_c", "doc_a", "doc_d", "doc_b")) {
            svc.applyWrite(Write.newBuilder().setUpdate(Document.newBuilder().setName(parent + "/col/" + id).build()).build(), Instant.now());
        }
        Obs p1 = new Obs();
        ctl.listDocuments(ListDocumentsRequest.newBuilder().setParent(parent).setCollectionId("col").setPageSize(2).build(), p1);
        List<String> names1 = p1.resp.getDocumentsList().stream().map(Document::getName).toList();
        if (!names1.equals(List.of(parent + "/col/doc_a", parent + "/col/doc_b")) || !"doc_b".equals(p1.resp.getNextPageToken())) {
            throw new AssertionError("page1=" + names1 + " next=" + p1.resp.getNextPageToken());
        }
        Obs p2 = new Obs();
        ctl.listDocuments(ListDocumentsRequest.newBuilder().setParent(parent).setCollectionId("col").setPageSize(2).setPageToken(p1.resp.getNextPageToken()).build(), p2);
        List<String> names2 = p2.resp.getDocumentsList().stream().map(Document::getName).toList();
        if (!names2.equals(List.of(parent + "/col/doc_c", parent + "/col/doc_d")) || !p2.resp.getNextPageToken().isEmpty()) {
            throw new AssertionError("page2=" + names2 + " next=" + p2.resp.getNextPageToken());
        }
    }
}
""", encoding='utf-8')
        cp = os.pathsep.join([str(exe.parent / 'app/*'), str(exe.parent / 'lib/main/*'), str(exe.parent / 'lib/boot/*')])
        subprocess.run([os.environ.get('JAVAC', 'javac'), '-cp', cp, '-d', str(tmp), str(probe)], check=True, timeout=30)
        subprocess.run([os.environ.get('JAVA', 'java'), '-cp', os.pathsep.join([str(tmp), cp]), 'io.floci.gcp.services.firestore.CheckFirestorePagination'], check=True, timeout=30)
        print('PASS: Firestore ListDocuments sorted pagination (pageSize, pageToken, nextPageToken)')
