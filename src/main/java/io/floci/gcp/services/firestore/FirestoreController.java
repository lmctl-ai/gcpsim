/* modified from floci-gcp 0.11.0 0fa563c603c1876a09b050ffdce0d96bac00fedc
   See durable-memory/ack-deadline-overlay.md and overlay.lock.json. */
package io.floci.gcp.services.firestore;

import com.google.firestore.v1.AggregationResult;
import com.google.firestore.v1.BatchGetDocumentsRequest;
import com.google.firestore.v1.BatchGetDocumentsResponse;
import com.google.firestore.v1.BatchWriteRequest;
import com.google.firestore.v1.BatchWriteResponse;
import com.google.firestore.v1.BeginTransactionRequest;
import com.google.firestore.v1.BeginTransactionResponse;
import com.google.firestore.v1.CommitRequest;
import com.google.firestore.v1.CommitResponse;
import com.google.firestore.v1.CreateDocumentRequest;
import com.google.firestore.v1.DeleteDocumentRequest;
import com.google.firestore.v1.Document;
import com.google.firestore.v1.DocumentChange;
import com.google.firestore.v1.FirestoreGrpc;
import com.google.firestore.v1.GetDocumentRequest;
import com.google.firestore.v1.ListCollectionIdsRequest;
import com.google.firestore.v1.ListCollectionIdsResponse;
import com.google.firestore.v1.ListDocumentsRequest;
import com.google.firestore.v1.ListDocumentsResponse;
import com.google.firestore.v1.ListenRequest;
import com.google.firestore.v1.ListenResponse;
import com.google.firestore.v1.PartitionQueryRequest;
import com.google.firestore.v1.PartitionQueryResponse;
import com.google.firestore.v1.Precondition;
import com.google.firestore.v1.RollbackRequest;
import com.google.firestore.v1.RunAggregationQueryRequest;
import com.google.firestore.v1.RunAggregationQueryResponse;
import com.google.firestore.v1.RunQueryRequest;
import com.google.firestore.v1.RunQueryResponse;
import com.google.firestore.v1.StructuredAggregationQuery;
import com.google.firestore.v1.StructuredQuery;
import com.google.firestore.v1.Target;
import com.google.firestore.v1.TargetChange;
import com.google.firestore.v1.UpdateDocumentRequest;
import com.google.firestore.v1.Value;
import com.google.firestore.v1.Write;
import com.google.firestore.v1.WriteRequest;
import com.google.firestore.v1.WriteResponse;
import com.google.firestore.v1.WriteResult;
import com.google.protobuf.ByteString;
import com.google.protobuf.Empty;
import com.google.protobuf.Timestamp;
import com.google.rpc.Status;
import ai.lmctl.gcpsim.BearerGuard;
import io.floci.gcp.core.common.GcpException;
import io.floci.gcp.core.common.GcpGrpcController;
import io.floci.gcp.services.firestore.model.StoredDocument;
import io.floci.gcp.services.firestore.model.StoredValue;
import io.grpc.stub.StreamObserver;
import org.jboss.logging.Logger;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

public class FirestoreController extends FirestoreGrpc.FirestoreImplBase {

    private static final Logger LOG = Logger.getLogger(FirestoreController.class);
    private static final int DEFAULT_LIST_PAGE_SIZE = 300;
    private static final int MAX_LIST_PAGE_SIZE = 1000;

    private final FirestoreService service;

    FirestoreController(FirestoreService service) {
        this.service = service;
    }

    @Override
    public void commit(CommitRequest request, StreamObserver<CommitResponse> responseObserver) {
        LOG.debugf("commit database=%s writes=%d", request.getDatabase(), request.getWritesCount());
        try {
            BearerGuard.authorizeFirestoreGrpc("Commit", request);
            Instant commitTime = Instant.now();
            CommitResponse.Builder response = CommitResponse.newBuilder()
                    .setCommitTime(toTimestamp(commitTime.toString()));

            List<FirestoreService.WriteCommitResult> results = service.commit(
                    request.getWritesList(), request.getTransaction().toByteArray(), commitTime);
            for (FirestoreService.WriteCommitResult result : results) {
                WriteResult.Builder wr = WriteResult.newBuilder();
                if (result.updateTime() != null) {
                    wr.setUpdateTime(toTimestamp(result.updateTime()));
                }
                response.addWriteResults(wr.build());
            }

            responseObserver.onNext(response.build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("commit failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void getDocument(GetDocumentRequest request, StreamObserver<Document> responseObserver) {
        LOG.debugf("getDocument name=%s", request.getName());
        try {
            BearerGuard.authorizeFirestoreGrpc("GetDocument", request);
            Optional<StoredDocument> stored = service.getDocument(request.getName());
            if (!request.getTransaction().isEmpty()) {
                service.recordTransactionRead(request.getTransaction().toByteArray(), request.getName(),
                        stored.map(StoredDocument::getUpdateTime).orElse(null));
            }
            responseObserver.onNext(toProto(stored.orElseThrow(
                    () -> io.floci.gcp.core.common.GcpException.notFound(
                            "Document not found: " + request.getName()))));
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("getDocument failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void batchGetDocuments(BatchGetDocumentsRequest request,
            StreamObserver<BatchGetDocumentsResponse> responseObserver) {
        LOG.debugf("batchGetDocuments database=%s docs=%d", request.getDatabase(), request.getDocumentsCount());
        try {
            BearerGuard.authorizeFirestoreGrpc("BatchGetDocuments", request);
            Instant readTime = Instant.now();
            ByteString txId = request.getTransaction();
            boolean newTransaction = request.hasNewTransaction();
            if (newTransaction) {
                txId = ByteString.copyFrom(service.beginTransaction());
            }
            boolean first = true;
            for (String docName : request.getDocumentsList()) {
                Optional<StoredDocument> stored = service.getDocument(docName);
                if (!txId.isEmpty()) {
                    service.recordTransactionRead(txId.toByteArray(), docName,
                            stored.map(StoredDocument::getUpdateTime).orElse(null));
                }
                BatchGetDocumentsResponse.Builder resp = BatchGetDocumentsResponse.newBuilder()
                        .setReadTime(toTimestamp(readTime.toString()));
                if (newTransaction && first) {
                    resp.setTransaction(txId);
                    first = false;
                }
                if (stored.isPresent()) {
                    resp.setFound(toProto(stored.get()));
                } else {
                    resp.setMissing(docName);
                }
                responseObserver.onNext(resp.build());
            }
            if (newTransaction && first) {
                responseObserver.onNext(BatchGetDocumentsResponse.newBuilder()
                        .setReadTime(toTimestamp(readTime.toString()))
                        .setTransaction(txId)
                        .build());
            }
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("batchGetDocuments failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void runQuery(RunQueryRequest request, StreamObserver<RunQueryResponse> responseObserver) {
        LOG.debugf("runQuery parent=%s", request.getParent());
        try {
            BearerGuard.authorizeFirestoreGrpc("RunQuery", request);
            List<StoredDocument> results = authorizeAndRunQuery(request.getParent(), request.getStructuredQuery());
            Instant readTime = Instant.now();
            ByteString txId = request.getTransaction();
            if (request.hasNewTransaction()) {
                txId = ByteString.copyFrom(service.beginTransaction());
                // per firestore.proto, the new transaction id is a first response of its own
                // with no other fields set
                responseObserver.onNext(RunQueryResponse.newBuilder()
                        .setTransaction(txId)
                        .build());
            }

            for (StoredDocument doc : results) {
                if (!txId.isEmpty()) {
                    service.recordTransactionRead(txId.toByteArray(), doc.getName(), doc.getUpdateTime());
                }
                responseObserver.onNext(RunQueryResponse.newBuilder()
                        .setDocument(toProto(doc))
                        .setReadTime(toTimestamp(readTime.toString()))
                        .build());
            }

            // terminal message
            responseObserver.onNext(RunQueryResponse.newBuilder()
                    .setReadTime(toTimestamp(readTime.toString()))
                    .setDone(true)
                    .build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("runQuery failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void beginTransaction(BeginTransactionRequest request,
            StreamObserver<BeginTransactionResponse> responseObserver) {
        LOG.debugf("beginTransaction database=%s", request.getDatabase());
        try {
            BearerGuard.authorizeFirestoreGrpc("BeginTransaction", request);
            byte[] txId = service.beginTransaction();
            responseObserver.onNext(BeginTransactionResponse.newBuilder()
                    .setTransaction(ByteString.copyFrom(txId))
                    .build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("beginTransaction failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void rollback(RollbackRequest request, StreamObserver<Empty> responseObserver) {
        LOG.debugf("rollback database=%s", request.getDatabase());
        try {
            BearerGuard.authorizeFirestoreGrpc("Rollback", request);
            service.rollback(request.getTransaction().toByteArray());
            responseObserver.onNext(Empty.getDefaultInstance());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("rollback failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void listDocuments(ListDocumentsRequest request, StreamObserver<ListDocumentsResponse> responseObserver) {
        LOG.debugf("listDocuments parent=%s collection=%s", request.getParent(), request.getCollectionId());
        try {
            BearerGuard.authorizeFirestoreGrpc("ListDocuments", request);
            String parent = request.getParent();
            String collectionId = request.getCollectionId();
            if (parent.isBlank() || !parent.startsWith("projects/") || collectionId.isBlank() || collectionId.contains("/")) {
                throw GcpException.invalidArgument("parent and collectionId are required");
            }
            int requestedPageSize = request.getPageSize();
            if (requestedPageSize < 0 || requestedPageSize > MAX_LIST_PAGE_SIZE) {
                throw GcpException.invalidArgument("Invalid pageSize: " + requestedPageSize);
            }
            int pageSize = requestedPageSize == 0 ? DEFAULT_LIST_PAGE_SIZE : requestedPageSize;

            String rawOrder = request.getOrderBy().trim();
            boolean descending;
            String canonicalOrder;
            String normalizedOrder = rawOrder.replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
            if (normalizedOrder.isEmpty() || normalizedOrder.equals("__name__")
                    || normalizedOrder.equals("__name__ asc") || normalizedOrder.equals("__name__ ascending")) {
                descending = false;
                canonicalOrder = "__name__ asc";
            } else if (normalizedOrder.equals("__name__ desc") || normalizedOrder.equals("__name__ descending")) {
                descending = true;
                canonicalOrder = "__name__ desc";
            } else {
                throw GcpException.invalidArgument("Unsupported orderBy for ListDocuments: " + rawOrder);
            }

            String prefix = parent + "/" + collectionId + "/";
            Comparator<StoredDocument> cmp = Comparator.comparing(StoredDocument::getName);
            if (descending) {
                cmp = cmp.reversed();
            }

            List<StoredDocument> docs = authorizeAndRunQuery(parent,
                    StructuredQuery.newBuilder()
                            .addFrom(StructuredQuery.CollectionSelector.newBuilder()
                                    .setCollectionId(collectionId)
                                    .build())
                            .build())
                    .stream()
                    .sorted(cmp)
                    .toList();

            String token = request.getPageToken();
            if (!token.isEmpty()) {
                String decoded;
                try {
                    decoded = new String(Base64.getUrlDecoder().decode(token), StandardCharsets.UTF_8);
                } catch (IllegalArgumentException e) {
                    throw GcpException.invalidArgument("Invalid pageToken");
                }
                String[] parts = decoded.split("\n", -1);
                if (parts.length != 4 || !parts[0].equals(parent) || !parts[1].equals(collectionId)
                        || !parts[2].equals(canonicalOrder) || !parts[3].startsWith(prefix)
                        || parts[3].substring(prefix.length()).isEmpty()) {
                    throw GcpException.invalidArgument("pageToken does not match request parameters");
                }
                String cursorName = parts[3];
                boolean desc = descending;
                docs = docs.stream()
                        .filter(d -> desc ? d.getName().compareTo(cursorName) < 0 : d.getName().compareTo(cursorName) > 0)
                        .toList();
            }

            String nextToken = "";
            if (docs.size() > pageSize) {
                docs = docs.subList(0, pageSize);
                String lastName = docs.get(docs.size() - 1).getName();
                String rawNext = parent + "\n" + collectionId + "\n" + canonicalOrder + "\n" + lastName;
                nextToken = Base64.getUrlEncoder().withoutPadding()
                        .encodeToString(rawNext.getBytes(StandardCharsets.UTF_8));
            }

            ListDocumentsResponse.Builder resp = ListDocumentsResponse.newBuilder();
            docs.forEach(d -> resp.addDocuments(toProto(d)));
            if (!nextToken.isEmpty()) {
                resp.setNextPageToken(nextToken);
            }
            responseObserver.onNext(resp.build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("listDocuments failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void updateDocument(UpdateDocumentRequest request, StreamObserver<Document> responseObserver) {
        LOG.debugf("updateDocument name=%s", request.getDocument().getName());
        try {
            BearerGuard.authorizeFirestoreGrpc("UpdateDocument", request);
            Write.Builder write = Write.newBuilder()
                    .setUpdate(request.getDocument());
            if (request.hasUpdateMask()) {
                write.setUpdateMask(request.getUpdateMask());
            }
            if (request.hasCurrentDocument()) {
                write.setCurrentDocument(request.getCurrentDocument());
            }
            service.applyWrite(write.build(), Instant.now());
            StoredDocument stored = service.getDocument(request.getDocument().getName())
                    .orElseThrow();
            responseObserver.onNext(toProto(stored));
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("updateDocument failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void deleteDocument(DeleteDocumentRequest request, StreamObserver<Empty> responseObserver) {
        LOG.debugf("deleteDocument name=%s", request.getName());
        try {
            BearerGuard.authorizeFirestoreGrpc("DeleteDocument", request);
            Write.Builder write = Write.newBuilder().setDelete(request.getName());
            if (request.hasCurrentDocument()) {
                write.setCurrentDocument(request.getCurrentDocument());
            }
            service.applyWrite(write.build(), Instant.now());
            responseObserver.onNext(Empty.getDefaultInstance());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("deleteDocument failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void createDocument(CreateDocumentRequest request, StreamObserver<Document> responseObserver) {
        LOG.debugf("createDocument parent=%s collection=%s", request.getParent(), request.getCollectionId());
        try {
            BearerGuard.authorizeFirestoreGrpc("CreateDocument", request);
            String docId = request.getDocumentId().isEmpty()
                    ? java.util.UUID.randomUUID().toString()
                    : request.getDocumentId();
            String name = request.getParent() + "/" + request.getCollectionId() + "/" + docId;
            Document doc = request.getDocument().toBuilder().setName(name).build();
            Write write = Write.newBuilder()
                    .setUpdate(doc)
                    .setCurrentDocument(Precondition.newBuilder().setExists(false).build())
                    .build();
            service.applyWrite(write, Instant.now());
            responseObserver.onNext(toProto(service.getDocument(name).orElseThrow()));
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("createDocument failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void listCollectionIds(ListCollectionIdsRequest request,
            StreamObserver<ListCollectionIdsResponse> responseObserver) {
        LOG.debugf("listCollectionIds parent=%s", request.getParent());
        try {
            BearerGuard.authorizeFirestoreGrpc("ListCollectionIds", request);
            List<String> ids = service.listCollectionIds(request.getParent());
            responseObserver.onNext(ListCollectionIdsResponse.newBuilder()
                    .addAllCollectionIds(ids).build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("listCollectionIds failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void batchWrite(BatchWriteRequest request, StreamObserver<BatchWriteResponse> responseObserver) {
        LOG.debugf("batchWrite database=%s writes=%d", request.getDatabase(), request.getWritesCount());
        try {
            BearerGuard.authorizeFirestoreGrpc("BatchWrite", request);
            Instant commitTime = Instant.now();
            BatchWriteResponse.Builder resp = BatchWriteResponse.newBuilder();
            // BatchWrite is non-atomic: each write succeeds or fails independently
            for (Write write : request.getWritesList()) {
                try {
                    FirestoreService.WriteCommitResult result = service.applyWrite(write, commitTime);
                    WriteResult.Builder wr = WriteResult.newBuilder();
                    if (result.updateTime() != null) {
                        wr.setUpdateTime(toTimestamp(result.updateTime()));
                    }
                    resp.addWriteResults(wr.build());
                    resp.addStatus(Status.newBuilder().setCode(0).build());
                } catch (RuntimeException e) {
                    io.grpc.Status mapped = GcpGrpcController.grpcException(e).getStatus();
                    String message = mapped.getDescription();
                    resp.addWriteResults(WriteResult.getDefaultInstance());
                    resp.addStatus(Status.newBuilder()
                            .setCode(mapped.getCode().value())
                            .setMessage(message == null ? "" : message)
                            .build());
                }
            }
            responseObserver.onNext(resp.build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("batchWrite failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void runAggregationQuery(RunAggregationQueryRequest request,
            StreamObserver<RunAggregationQueryResponse> responseObserver) {
        LOG.debugf("runAggregationQuery parent=%s", request.getParent());
        try {
            BearerGuard.authorizeFirestoreGrpc("RunAggregationQuery", request);
            Instant readTime = Instant.now();
            AggregationResult.Builder agg = AggregationResult.newBuilder();
            if (request.hasStructuredAggregationQuery()) {
                StructuredAggregationQuery saq = request.getStructuredAggregationQuery();
                StructuredQuery sq = saq.hasStructuredQuery()
                        ? saq.getStructuredQuery()
                        : com.google.firestore.v1.StructuredQuery.getDefaultInstance();
                long count = authorizeAndRunQuery(request.getParent(), sq).size();
                for (StructuredAggregationQuery.Aggregation aggregation : saq.getAggregationsList()) {
                    String alias = aggregation.getAlias().isEmpty() ? "field_1" : aggregation.getAlias();
                    agg.putAggregateFields(alias,
                            Value.newBuilder().setIntegerValue(count).build());
                }
            }
            responseObserver.onNext(RunAggregationQueryResponse.newBuilder()
                    .setResult(agg.build())
                    .setReadTime(toTimestamp(readTime.toString()))
                    .build());
            responseObserver.onCompleted();
        } catch (Exception e) {
            LOG.warnf("runAggregationQuery failed: %s", e.getMessage());
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public void partitionQuery(PartitionQueryRequest request,
            StreamObserver<PartitionQueryResponse> responseObserver) {
        try {
            BearerGuard.authorizeFirestoreGrpc("PartitionQuery", request);
            responseObserver.onNext(PartitionQueryResponse.getDefaultInstance());
            responseObserver.onCompleted();
        } catch (Exception e) {
            GcpGrpcController.grpcError(responseObserver, e);
        }
    }

    @Override
    public StreamObserver<WriteRequest> write(StreamObserver<WriteResponse> responseObserver) {
        return new StreamObserver<>() {
            private volatile boolean failed = false;

            public void onNext(WriteRequest req) {
                if (failed) {
                    return;
                }
                try {
                    BearerGuard.authorizeFirestoreGrpc("Write", req);
                    Instant now = Instant.now();
                    WriteResponse.Builder resp = WriteResponse.newBuilder()
                            .setStreamId(req.getStreamId())
                            .setCommitTime(toTimestamp(now.toString()));
                    for (FirestoreService.WriteCommitResult r
                            : service.commit(req.getWritesList(), null, now)) {
                        WriteResult.Builder wr = WriteResult.newBuilder();
                        if (r.updateTime() != null) {
                            wr.setUpdateTime(toTimestamp(r.updateTime()));
                        }
                        resp.addWriteResults(wr.build());
                    }
                    responseObserver.onNext(resp.build());
                } catch (Exception e) {
                    failed = true;
                    LOG.warnf("write stream error: %s", e.getMessage());
                    responseObserver.onError(GcpGrpcController.grpcException(e));
                }
            }
            public void onError(Throwable t) { LOG.debugf("write stream closed by client: %s", t.getMessage()); }
            public void onCompleted() {
                if (!failed) {
                    responseObserver.onCompleted();
                }
            }
        };
    }

    @Override
    public StreamObserver<ListenRequest> listen(StreamObserver<ListenResponse> responseObserver) {
        return new StreamObserver<>() {
            private volatile boolean initialized = false;
            private volatile boolean failed = false;

            public void onNext(ListenRequest req) {
                if (failed) {
                    return;
                }
                try {
                    BearerGuard.authorizeFirestoreGrpc("Listen", req);
                    if (req.hasAddTarget() && !initialized) {
                        initialized = true;
                        Target target = req.getAddTarget();
                        int targetId = target.getTargetId();
                        Instant now = Instant.now();
                        Timestamp readTime = toTimestamp(now.toString());

                        responseObserver.onNext(ListenResponse.newBuilder()
                                .setTargetChange(TargetChange.newBuilder()
                                        .setTargetChangeType(TargetChange.TargetChangeType.ADD)
                                        .addTargetIds(targetId)
                                        .setReadTime(readTime)
                                        .build())
                                .build());

                        if (target.hasQuery()) {
                            Target.QueryTarget qt = target.getQuery();
                            List<StoredDocument> docs = service.runQuery(qt.getParent(),
                                    qt.hasStructuredQuery() ? qt.getStructuredQuery()
                                            : StructuredQuery.getDefaultInstance());
                            for (StoredDocument doc : docs) {
                                responseObserver.onNext(ListenResponse.newBuilder()
                                        .setDocumentChange(DocumentChange.newBuilder()
                                                .setDocument(toProto(doc))
                                                .addTargetIds(targetId)
                                                .build())
                                        .build());
                            }
                        }

                        responseObserver.onNext(ListenResponse.newBuilder()
                                .setTargetChange(TargetChange.newBuilder()
                                        .setTargetChangeType(TargetChange.TargetChangeType.CURRENT)
                                        .addTargetIds(targetId)
                                        .setReadTime(readTime)
                                        .build())
                                .build());
                    }
                } catch (Exception e) {
                    failed = true;
                    LOG.warnf("listen error: %s", e.getMessage());
                    responseObserver.onError(GcpGrpcController.grpcException(e));
                }
            }

            public void onError(Throwable t) {
                LOG.debugf("listen stream closed by client: %s", t.getMessage());
            }

            public void onCompleted() {
                if (!failed) {
                    responseObserver.onCompleted();
                }
            }
        };
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    private List<StoredDocument> authorizeAndRunQuery(String parent, StructuredQuery query) {
        if (query.getFromCount() > 1) {
            throw GcpException.invalidArgument("StructuredQuery supports at most one collection selector");
        }
        StructuredQuery scopeQuery = StructuredQuery.newBuilder()
                .addAllFrom(query.getFromList())
                .build();
        for (StoredDocument doc : service.runQuery(parent, scopeQuery)) {
            BearerGuard.authorizeFirestoreDocumentRead(doc.getName());
        }
        List<StoredDocument> results = service.runQuery(parent, query);
        for (StoredDocument doc : results) {
            BearerGuard.authorizeFirestoreDocumentRead(doc.getName());
        }
        return results;
    }

    private static Document toProto(StoredDocument stored) {
        Document.Builder builder = Document.newBuilder()
                .setName(stored.getName())
                .setCreateTime(toTimestamp(stored.getCreateTime()))
                .setUpdateTime(toTimestamp(stored.getUpdateTime()));
        if (stored.getFields() != null) {
            stored.getFields().forEach((k, v) -> builder.putFields(k, v.toProto()));
        }
        return builder.build();
    }

    static Timestamp toTimestamp(String isoTime) {
        if (isoTime == null) return Timestamp.getDefaultInstance();
        try {
            Instant instant = Instant.parse(isoTime);
            return Timestamp.newBuilder()
                    .setSeconds(instant.getEpochSecond())
                    .setNanos(instant.getNano())
                    .build();
        } catch (Exception e) {
            return Timestamp.getDefaultInstance();
        }
    }
}
