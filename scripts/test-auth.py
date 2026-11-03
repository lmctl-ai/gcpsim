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
        cp = os.pathsep.join([str(exe.parent / 'app/*'), str(exe.parent / 'lib/main/*'), str(exe.parent / 'lib/boot/*')])
        probe = tmp / 'SimAuthProbe.java'
        probe.write_text("""import com.google.firestore.v1.*;
import com.google.protobuf.Empty;
import io.grpc.*;
import io.grpc.protobuf.ProtoUtils;
import io.grpc.stub.ClientCalls;
import io.grpc.stub.MetadataUtils;
import io.grpc.stub.StreamObserver;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public class SimAuthProbe {
    static final String ROOT = "projects/gcpsim-local/databases/(default)/documents";
    static final String FOREIGN = "projects/foreign-project/databases/(default)/documents";

    static FirestoreGrpc.FirestoreBlockingStub stub(ManagedChannel c, String token) {
        Metadata m = new Metadata();
        if (token != null) {
            m.put(Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER), "Bearer " + token);
        }
        return FirestoreGrpc.newBlockingStub(c)
                .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(m))
                .withDeadlineAfter(10, TimeUnit.SECONDS);
    }

    static FirestoreGrpc.FirestoreStub asyncStub(ManagedChannel c, String token) {
        Metadata m = new Metadata();
        if (token != null) {
            m.put(Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER), "Bearer " + token);
        }
        return FirestoreGrpc.newStub(c)
                .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(m))
                .withDeadlineAfter(10, TimeUnit.SECONDS);
    }

    static void seed(FirestoreGrpc.FirestoreBlockingStub a, String root, String col, String id) {
        a.createDocument(CreateDocumentRequest.newBuilder()
                .setParent(root)
                .setCollectionId(col)
                .setDocumentId(id)
                .setDocument(Document.newBuilder().putFields("value", Value.newBuilder().setStringValue("private").build()))
                .build());
    }

    static void expectStatus(Status.Code expected, Runnable fn, String label) {
        try {
            fn.run();
            throw new AssertionError("Expected " + expected + " for " + label + ", but succeeded");
        } catch (StatusRuntimeException e) {
            if (e.getStatus().getCode() != expected) {
                throw new AssertionError("Expected " + expected + " for " + label + ", got " + e.getStatus());
            }
        }
    }

    public static void main(String[] args) throws Exception {
        ManagedChannel c = ManagedChannelBuilder.forTarget(args[0]).usePlaintext().build();
        try {
            var admin = stub(c, "admin-token");
            var local = stub(c, "local-token");
            var update = stub(c, "update-token");
            var bad = stub(c, "invalid-token");

            seed(admin, FOREIGN, "secret", "foreign");
            seed(admin, ROOT, "secret", "commit");
            seed(admin, ROOT, "secret", "batch");
            seed(admin, ROOT, "secret", "stream");
            seed(admin, ROOT, "forbidden-col", "blocked-doc");
            seed(admin, ROOT, "allowed-col", "ok-doc");

            // Unauthenticated token rejected with UNAUTHENTICATED
            expectStatus(Status.Code.UNAUTHENTICATED,
                    () -> bad.getDocument(GetDocumentRequest.newBuilder().setName(ROOT + "/allowed-col/ok-doc").build()),
                    "invalid bearer token");

            // Allowed local project read succeeds, denied collection in local project fails with PERMISSION_DENIED
            var okDoc = local.getDocument(GetDocumentRequest.newBuilder().setName(ROOT + "/allowed-col/ok-doc").build());
            if (!okDoc.getName().endsWith("/allowed-col/ok-doc")) {
                throw new AssertionError("Unexpected doc: " + okDoc.getName());
            }
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> local.getDocument(GetDocumentRequest.newBuilder().setName(ROOT + "/forbidden-col/blocked-doc").build()),
                    "collection-specific deny");

            // Cross-project read and write blocked with PERMISSION_DENIED
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> local.getDocument(GetDocumentRequest.newBuilder().setName(FOREIGN + "/secret/foreign").build()),
                    "cross-project getDocument");
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> seed(local, FOREIGN, "secret", "foreign-write"),
                    "cross-project createDocument");
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> local.runQuery(RunQueryRequest.newBuilder().setParent(FOREIGN).build()).hasNext(),
                    "cross-project runQuery");

            // Direct delete, Commit delete, BatchWrite delete, and streaming Write delete blocked for update-only token
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> update.deleteDocument(DeleteDocumentRequest.newBuilder().setName(ROOT + "/secret/commit").build()),
                    "direct deleteDocument");
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> update.commit(CommitRequest.newBuilder()
                            .setDatabase("projects/gcpsim-local/databases/(default)")
                            .addWrites(Write.newBuilder().setDelete(ROOT + "/secret/commit"))
                            .build()),
                    "commit delete");
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> update.batchWrite(BatchWriteRequest.newBuilder()
                            .setDatabase("projects/gcpsim-local/databases/(default)")
                            .addWrites(Write.newBuilder().setDelete(ROOT + "/secret/batch"))
                            .build()),
                    "batchWrite delete");

            CountDownLatch writeLatch = new CountDownLatch(1);
            AtomicReference<Throwable> writeErr = new AtomicReference<>();
            StreamObserver<WriteRequest> writeStream = asyncStub(c, "update-token").write(new StreamObserver<WriteResponse>() {
                public void onNext(WriteResponse v) {}
                public void onError(Throwable t) { writeErr.set(t); writeLatch.countDown(); }
                public void onCompleted() { writeLatch.countDown(); }
            });
            writeStream.onNext(WriteRequest.newBuilder()
                    .setDatabase("projects/gcpsim-local/databases/(default)")
                    .addWrites(Write.newBuilder().setDelete(ROOT + "/secret/stream"))
                    .build());
            writeStream.onCompleted();
            if (!writeLatch.await(5, TimeUnit.SECONDS) || !(writeErr.get() instanceof StatusRuntimeException sre)
                    || sre.getStatus().getCode() != Status.Code.PERMISSION_DENIED) {
                throw new AssertionError("Expected PERMISSION_DENIED on streaming Write delete, got " + writeErr.get());
            }

            // Verify all target documents survived forbidden delete attempts
            for (String id : List.of("commit", "batch", "stream")) {
                var doc = admin.getDocument(GetDocumentRequest.newBuilder().setName(ROOT + "/secret/" + id).build());
                if (!doc.getName().endsWith("/" + id)) {
                    throw new AssertionError("Document missing after forbidden delete: " + id);
                }
            }

            // Listen and UnknownMethod fail closed with PERMISSION_DENIED
            CountDownLatch listenLatch = new CountDownLatch(1);
            AtomicReference<Throwable> listenErr = new AtomicReference<>();
            StreamObserver<ListenRequest> listenStream = asyncStub(c, "admin-token").listen(new StreamObserver<ListenResponse>() {
                public void onNext(ListenResponse v) {}
                public void onError(Throwable t) { listenErr.set(t); listenLatch.countDown(); }
                public void onCompleted() { listenLatch.countDown(); }
            });
            listenStream.onNext(ListenRequest.newBuilder().setDatabase("projects/gcpsim-local/databases/(default)").build());
            listenStream.onCompleted();
            if (!listenLatch.await(5, TimeUnit.SECONDS) || !(listenErr.get() instanceof StatusRuntimeException lsre)
                    || lsre.getStatus().getCode() != Status.Code.PERMISSION_DENIED) {
                throw new AssertionError("Expected PERMISSION_DENIED on Listen, got " + listenErr.get());
            }

            var unknown = MethodDescriptor.<Empty, Empty>newBuilder()
                    .setType(MethodDescriptor.MethodType.UNARY)
                    .setFullMethodName("google.firestore.v1.Firestore/UnknownMethod")
                    .setRequestMarshaller(ProtoUtils.marshaller(Empty.getDefaultInstance()))
                    .setResponseMarshaller(ProtoUtils.marshaller(Empty.getDefaultInstance()))
                    .build();
            Metadata m = new Metadata();
            m.put(Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER), "Bearer admin-token");
            Channel ch = ClientInterceptors.intercept(c, MetadataUtils.newAttachHeadersInterceptor(m));
            expectStatus(Status.Code.PERMISSION_DENIED,
                    () -> ClientCalls.blockingUnaryCall(ch, unknown, CallOptions.DEFAULT.withDeadlineAfter(5, TimeUnit.SECONDS), Empty.getDefaultInstance()),
                    "UnknownMethod");

            // Pagination, orderBy, bound pageToken, and pageSize validation
            for (String id : List.of("a", "b", "c")) {
                seed(admin, ROOT, "pages", id);
                seed(admin, ROOT, "otherpages", id);
            }
            var p1 = admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setPageSize(2).build());
            var p2 = admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setPageSize(2).setPageToken(p1.getNextPageToken()).build());
            if (p1.getDocumentsCount() != 2 || p2.getDocumentsCount() != 1 || !p2.getNextPageToken().isEmpty()) {
                throw new AssertionError("Unexpected pagination counts: " + p1.getDocumentsCount() + "+" + p2.getDocumentsCount());
            }
            if (!p1.getDocuments(0).getName().endsWith("/pages/a") || !p1.getDocuments(1).getName().endsWith("/pages/b")
                    || !p2.getDocuments(0).getName().endsWith("/pages/c")) {
                throw new AssertionError("Unexpected ascending document order");
            }
            expectStatus(Status.Code.INVALID_ARGUMENT,
                    () -> admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("otherpages").setPageSize(2).setPageToken(p1.getNextPageToken()).build()),
                    "cross-collection pageToken replay");
            expectStatus(Status.Code.INVALID_ARGUMENT,
                    () -> admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setPageSize(2).setOrderBy("__name__ desc").setPageToken(p1.getNextPageToken()).build()),
                    "cross-order pageToken replay");
            var desc1 = admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setPageSize(2).setOrderBy("__name__ desc").build());
            var desc2 = admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setPageSize(2).setOrderBy("__name__ desc").setPageToken(desc1.getNextPageToken()).build());
            if (!desc1.getDocuments(0).getName().endsWith("/pages/c") || !desc1.getDocuments(1).getName().endsWith("/pages/b")
                    || !desc2.getDocuments(0).getName().endsWith("/pages/a") || !desc2.getNextPageToken().isEmpty()) {
                throw new AssertionError("Unexpected descending document order");
            }
            expectStatus(Status.Code.INVALID_ARGUMENT,
                    () -> admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setPageSize(-1).build()),
                    "negative pageSize");
            expectStatus(Status.Code.INVALID_ARGUMENT,
                    () -> admin.listDocuments(ListDocumentsRequest.newBuilder().setParent(ROOT).setCollectionId("pages").setOrderBy("unsupported_field").build()),
                    "unsupported orderBy");
        } finally {
            c.shutdownNow().awaitTermination(5, TimeUnit.SECONDS);
        }
    }
}
""", encoding='utf-8')
        subprocess.run([os.environ.get('JAVAC', 'javac'), '-cp', cp, '-d', str(tmp), str(probe)], check=True, timeout=30)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            grpc_port = sock.getsockname()[1]
        grpc_auth = {
            'tokens': {
                'admin-token': 'user:admin@example.test',
                'local-token': 'user:local@example.test',
                'update-token': 'user:update@example.test',
            },
            'Statement': [
                {'Effect': 'Allow', 'ActionGlob': '*', 'ResourceGlob': '*', 'Principal': 'user:admin@example.test'},
                {'Effect': 'Allow', 'ActionGlob': '*', 'ResourceGlob': 'projects/gcpsim-local', 'Principal': 'user:local@example.test'},
                {'Effect': 'Deny', 'ActionGlob': '*', 'ResourceGlob': 'projects/foreign-project*', 'Principal': 'user:local@example.test'},
                {'Effect': 'Deny', 'ActionGlob': '*', 'ResourceGlob': 'projects/gcpsim-local/databases/(default)/documents/forbidden-col*', 'Principal': 'user:local@example.test'},
                {'Effect': 'Allow', 'Action': 'datastore.entities.update', 'ResourceGlob': 'projects/gcpsim-local', 'Principal': 'user:update@example.test'},
                {'Effect': 'Deny', 'Action': 'datastore.entities.delete', 'ResourceGlob': '*', 'Principal': 'user:update@example.test'},
            ],
        }
        grpc_auth_path = tmp / 'grpc-auth.json'
        grpc_auth_path.write_text(json.dumps(grpc_auth), encoding='utf-8')
        with (tmp / 'grpc-server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen(cmd + ['serve', '--port', str(grpc_port), '--auth', 'enforce', '--auth-config', str(grpc_auth_path)],
                                      env=env, cwd=tmp, stdout=log, stderr=log)
            try:
                for _ in range(240):
                    if server.poll() is not None:
                        raise AssertionError('gRPC server exited before readiness')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{grpc_port}/health', timeout=2) as r:
                            if r.status == 200:
                                break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise AssertionError('gRPC server readiness timeout')
                subprocess.run([os.environ.get('JAVA', 'java'), '-cp', os.pathsep.join([str(tmp), cp]),
                                'SimAuthProbe', f'127.0.0.1:{grpc_port}'], check=True, timeout=60)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
        print('PASS: Firestore gRPC resource/batch/stream auth enforcement and bound ListDocuments pagination/orderBy')
