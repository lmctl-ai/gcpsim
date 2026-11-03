package ai.lmctl.gcpsim;

import com.google.firestore.v1.BatchGetDocumentsRequest;
import com.google.firestore.v1.BatchWriteRequest;
import com.google.firestore.v1.BeginTransactionRequest;
import com.google.firestore.v1.CommitRequest;
import com.google.firestore.v1.CreateDocumentRequest;
import com.google.firestore.v1.DeleteDocumentRequest;
import com.google.firestore.v1.GetDocumentRequest;
import com.google.firestore.v1.ListCollectionIdsRequest;
import com.google.firestore.v1.ListDocumentsRequest;
import com.google.firestore.v1.Precondition;
import com.google.firestore.v1.RollbackRequest;
import com.google.firestore.v1.RunAggregationQueryRequest;
import com.google.firestore.v1.RunQueryRequest;
import com.google.firestore.v1.StructuredQuery;
import com.google.firestore.v1.UpdateDocumentRequest;
import com.google.firestore.v1.Write;
import com.google.firestore.v1.WriteRequest;
import io.floci.gcp.services.iam.IamPolicyEvaluator;
import io.floci.gcp.services.iam.IamPrincipal;
import io.floci.gcp.services.iam.IamResource;
import io.floci.gcp.services.iam.authorization.IamGrpcAuthorizationInterceptor;
import io.grpc.Context;
import io.grpc.Status;
import io.vertx.core.http.HttpVersion;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.RoutingContext;
import jakarta.annotation.Priority;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Runs before REST and gRPC routing; alternate transports cannot bypass enforcement. */
@ApplicationScoped
public class BearerGuard {
    private static final Pattern BEARER = Pattern.compile("(?i:Bearer) ([A-Za-z0-9._~+/-]+=*)");
    private static final String FIRESTORE_PREFIX = "/google.firestore.v1.Firestore/";
    private static final Context.Key<String> GRPC_AUTH_KEY;
    static {
        try {
            Field f = IamGrpcAuthorizationInterceptor.class.getDeclaredField("AUTHORIZATION");
            f.setAccessible(true);
            @SuppressWarnings("unchecked")
            Context.Key<String> key = (Context.Key<String>) f.get(null);
            GRPC_AUTH_KEY = key;
        } catch (ReflectiveOperationException e) {
            throw new ExceptionInInitializerError(e);
        }
    }
    private static final Set<String> SUPPORTED_FIRESTORE_METHODS = Set.of(
            "GetDocument",
            "CreateDocument",
            "UpdateDocument",
            "DeleteDocument",
            "ListDocuments",
            "ListCollectionIds",
            "BeginTransaction",
            "Rollback",
            "Commit",
            "BatchWrite",
            "Write",
            "BatchGetDocuments",
            "RunQuery",
            "RunAggregationQuery"
    );

    private static volatile IamPolicyEvaluator staticEvaluator;

    @Inject
    IamPolicyEvaluator evaluator;

    void init(@Observes @Priority(1) Router router) {
        staticEvaluator = evaluator;
        router.route().order(Integer.MIN_VALUE).handler(this::guard);
    }

    void guard(RoutingContext ctx) {
        if (!AuthConfig.enforce || (ctx.normalizedPath().equals("/health") && ctx.request().method().name().equals("GET"))) {
            ctx.next();
            return;
        }
        List<String> headers = ctx.request().headers().getAll("Authorization");
        Matcher bearer = BEARER.matcher(headers.size() == 1 ? headers.getFirst() : "");
        if (!bearer.matches() || !AuthConfig.tokens.containsKey(bearer.group(1))) {
            ctx.response().putHeader("WWW-Authenticate", "Bearer");
            error(ctx, 401, "UNAUTHENTICATED", "Request requires valid authentication credentials.");
            return;
        }
        String path = ctx.normalizedPath();
        if (path.startsWith(FIRESTORE_PREFIX)) {
            String method = path.substring(FIRESTORE_PREFIX.length());
            if (!SUPPORTED_FIRESTORE_METHODS.contains(method) || ctx.request().version() != HttpVersion.HTTP_2) {
                error(ctx, 403, "PERMISSION_DENIED", "Request is not permitted by the emulator auth configuration.");
                return;
            }
            ctx.next();
            return;
        }
        if (!(path.startsWith("/storage/v1/") || path.startsWith("/upload/storage/v1/")
                || path.startsWith("/download/storage/v1/") || path.startsWith("/v1/projects/"))) {
            error(ctx, 403, "PERMISSION_DENIED", "Operation is outside the enforced REST subset.");
            return;
        }
        ctx.next();
    }

    public static void authorizeFirestoreGrpc(String method, Object message) {
        if (!AuthConfig.enforce) {
            return;
        }
        String rawAuth = GRPC_AUTH_KEY.get();
        Matcher bearer = BEARER.matcher(rawAuth == null ? "" : rawAuth);
        if (!bearer.matches() || !AuthConfig.tokens.containsKey(bearer.group(1))) {
            throw Status.UNAUTHENTICATED.withDescription("Request requires valid authentication credentials.").asRuntimeException();
        }
        String principal = AuthConfig.tokens.get(bearer.group(1));
        if (method == null || !SUPPORTED_FIRESTORE_METHODS.contains(method)) {
            throw Status.PERMISSION_DENIED.withDescription("Operation is outside the enforced Firestore gRPC subset.").asRuntimeException();
        }
        try {
            authorizeFirestoreMessage(principal, method, message);
        } catch (Exception e) {
            throw Status.PERMISSION_DENIED.withDescription("Request is not permitted by the emulator auth configuration.").asRuntimeException();
        }
    }

    public static void authorizeFirestoreDocumentRead(String docName) {
        if (!AuthConfig.enforce) {
            return;
        }
        String rawAuth = GRPC_AUTH_KEY.get();
        Matcher bearer = BEARER.matcher(rawAuth == null ? "" : rawAuth);
        if (!bearer.matches() || !AuthConfig.tokens.containsKey(bearer.group(1))) {
            throw Status.UNAUTHENTICATED.withDescription("Request requires valid authentication credentials.").asRuntimeException();
        }
        String principal = AuthConfig.tokens.get(bearer.group(1));
        try {
            requireDocumentPath(docName);
            authorizeResource(principal, "datastore.entities.get", docName);
            authorizeResource(principal, "datastore.entities.list", docName);
        } catch (Exception e) {
            throw Status.PERMISSION_DENIED.withDescription("Request is not permitted by the emulator auth configuration.").asRuntimeException();
        }
    }

    static void authorizeFirestoreMessage(String principal, String method, Object message) {
        switch (method) {
            case "GetDocument" -> {
                GetDocumentRequest req = (GetDocumentRequest) message;
                requireDocumentPath(req.getName());
                authorizeResource(principal, "datastore.entities.get", req.getName());
            }
            case "CreateDocument" -> {
                CreateDocumentRequest req = (CreateDocumentRequest) message;
                requireParentPath(req.getParent());
                requireSingleSegment(req.getCollectionId());
                String target = req.getParent() + "/" + req.getCollectionId();
                if (!req.getDocumentId().isEmpty()) {
                    requireSingleSegment(req.getDocumentId());
                    target += "/" + req.getDocumentId();
                }
                authorizeResource(principal, "datastore.entities.create", target);
            }
            case "UpdateDocument" -> {
                UpdateDocumentRequest req = (UpdateDocumentRequest) message;
                if (!req.hasDocument()) {
                    throw new IllegalArgumentException("Document is required");
                }
                String doc = req.getDocument().getName();
                requireDocumentPath(doc);
                authorizeWritePrecondition(principal, doc, req.hasCurrentDocument() ? req.getCurrentDocument() : null);
            }
            case "DeleteDocument" -> {
                DeleteDocumentRequest req = (DeleteDocumentRequest) message;
                requireDocumentPath(req.getName());
                authorizeResource(principal, "datastore.entities.delete", req.getName());
            }
            case "ListDocuments" -> {
                ListDocumentsRequest req = (ListDocumentsRequest) message;
                requireParentPath(req.getParent());
                requireSingleSegment(req.getCollectionId());
                String target = req.getParent() + "/" + req.getCollectionId();
                authorizeResource(principal, "datastore.entities.get", target);
                authorizeResource(principal, "datastore.entities.list", target);
            }
            case "ListCollectionIds" -> {
                ListCollectionIdsRequest req = (ListCollectionIdsRequest) message;
                requireParentPath(req.getParent());
                authorizeResource(principal, "datastore.entities.list", req.getParent());
            }
            case "BeginTransaction" -> {
                BeginTransactionRequest req = (BeginTransactionRequest) message;
                requireDatabasePath(req.getDatabase());
                authorizeResource(principal, "datastore.databases.get", req.getDatabase());
            }
            case "Rollback" -> {
                RollbackRequest req = (RollbackRequest) message;
                requireDatabasePath(req.getDatabase());
                authorizeResource(principal, "datastore.databases.get", req.getDatabase());
            }
            case "Commit" -> {
                CommitRequest req = (CommitRequest) message;
                requireDatabasePath(req.getDatabase());
                if (req.getWritesCount() == 0) {
                    authorizeAnyEntityMutation(principal, req.getDatabase());
                }
                for (Write w : req.getWritesList()) {
                    authorizeWrite(principal, req.getDatabase(), w);
                }
            }
            case "BatchWrite" -> {
                BatchWriteRequest req = (BatchWriteRequest) message;
                requireDatabasePath(req.getDatabase());
                if (req.getWritesCount() == 0) {
                    authorizeAnyEntityMutation(principal, req.getDatabase());
                }
                for (Write w : req.getWritesList()) {
                    authorizeWrite(principal, req.getDatabase(), w);
                }
            }
            case "Write" -> {
                WriteRequest req = (WriteRequest) message;
                String db = req.getDatabase();
                if (!db.isEmpty()) {
                    requireDatabasePath(db);
                    if (req.getWritesCount() == 0) {
                        authorizeAnyEntityMutation(principal, db);
                    }
                } else if (req.getWritesCount() == 0) {
                    throw new IllegalArgumentException("WriteRequest database or writes required");
                }
                for (Write w : req.getWritesList()) {
                    authorizeWrite(principal, db.isEmpty() ? null : db, w);
                }
            }
            case "BatchGetDocuments" -> {
                BatchGetDocumentsRequest req = (BatchGetDocumentsRequest) message;
                requireDatabasePath(req.getDatabase());
                if (req.getDocumentsCount() == 0) {
                    authorizeResource(principal, "datastore.entities.get", req.getDatabase());
                }
                for (String doc : req.getDocumentsList()) {
                    requireDocumentInDatabase(req.getDatabase(), doc);
                    authorizeResource(principal, "datastore.entities.get", doc);
                }
            }
            case "RunQuery" -> {
                RunQueryRequest req = (RunQueryRequest) message;
                requireParentPath(req.getParent());
                if (req.hasStructuredQuery() && req.getStructuredQuery().getFromCount() > 0) {
                    for (StructuredQuery.CollectionSelector sel : req.getStructuredQuery().getFromList()) {
                        requireSingleSegment(sel.getCollectionId());
                        String target = req.getParent() + "/" + sel.getCollectionId();
                        authorizeResource(principal, "datastore.entities.get", target);
                        authorizeResource(principal, "datastore.entities.list", target);
                    }
                } else {
                    authorizeResource(principal, "datastore.entities.get", req.getParent());
                    authorizeResource(principal, "datastore.entities.list", req.getParent());
                }
            }
            case "RunAggregationQuery" -> {
                RunAggregationQueryRequest req = (RunAggregationQueryRequest) message;
                requireParentPath(req.getParent());
                if (req.hasStructuredAggregationQuery()
                        && req.getStructuredAggregationQuery().hasStructuredQuery()
                        && req.getStructuredAggregationQuery().getStructuredQuery().getFromCount() > 0) {
                    for (StructuredQuery.CollectionSelector sel : req.getStructuredAggregationQuery().getStructuredQuery().getFromList()) {
                        requireSingleSegment(sel.getCollectionId());
                        String target = req.getParent() + "/" + sel.getCollectionId();
                        authorizeResource(principal, "datastore.entities.get", target);
                        authorizeResource(principal, "datastore.entities.list", target);
                    }
                } else {
                    authorizeResource(principal, "datastore.entities.get", req.getParent());
                    authorizeResource(principal, "datastore.entities.list", req.getParent());
                }
            }
            default -> throw new IllegalArgumentException("Unsupported Firestore method: " + method);
        }
    }

    private static void authorizeWrite(String principal, String expectedDatabase, Write w) {
        Precondition pre = w.hasCurrentDocument() ? w.getCurrentDocument() : null;
        switch (w.getOperationCase()) {
            case UPDATE -> {
                String doc = w.getUpdate().getName();
                if (expectedDatabase != null) {
                    requireDocumentInDatabase(expectedDatabase, doc);
                } else {
                    requireDocumentPath(doc);
                }
                authorizeWritePrecondition(principal, doc, pre);
            }
            case DELETE -> {
                String doc = w.getDelete();
                if (expectedDatabase != null) {
                    requireDocumentInDatabase(expectedDatabase, doc);
                } else {
                    requireDocumentPath(doc);
                }
                authorizeResource(principal, "datastore.entities.delete", doc);
            }
            case TRANSFORM -> {
                String doc = w.getTransform().getDocument();
                if (expectedDatabase != null) {
                    requireDocumentInDatabase(expectedDatabase, doc);
                } else {
                    requireDocumentPath(doc);
                }
                authorizeWritePrecondition(principal, doc, pre);
            }
            default -> throw new IllegalArgumentException("Write operation required");
        }
    }

    private static void authorizeWritePrecondition(String principal, String doc, Precondition pre) {
        if (pre != null) {
            switch (pre.getConditionTypeCase()) {
                case EXISTS -> {
                    if (pre.getExists()) {
                        authorizeResource(principal, "datastore.entities.update", doc);
                    } else {
                        authorizeResource(principal, "datastore.entities.create", doc);
                    }
                    return;
                }
                case UPDATE_TIME -> {
                    authorizeResource(principal, "datastore.entities.update", doc);
                    return;
                }
                default -> {
                    // Fall through to unrestricted upsert requiring both create and update.
                }
            }
        }
        authorizeResource(principal, "datastore.entities.create", doc);
        authorizeResource(principal, "datastore.entities.update", doc);
    }

    private static void authorizeAnyEntityMutation(String principal, String resourcePath) {
        for (String action : List.of("datastore.entities.create", "datastore.entities.update", "datastore.entities.delete")) {
            try {
                authorizeResource(principal, action, resourcePath);
                return;
            } catch (SecurityException ignored) {
            }
        }
        throw new SecurityException("Not allowed by policy");
    }

    private static void authorizeResource(String principal, String action, String resourcePath) {
        List<String> candidates = resourceHierarchy(resourcePath);
        IamResource iamResource = IamResource.projectChild("firestore.googleapis.com", "Entity", resourcePath, resourcePath);
        boolean allowed = !AuthConfig.hasPolicies
                || (staticEvaluator != null && staticEvaluator.isAllowed(new IamPrincipal(principal), action, iamResource, AuthConfig.bindings));
        for (AuthConfig.Rule rule : AuthConfig.rules) {
            boolean matched = false;
            for (String candidate : candidates) {
                if (rule.matches(action, candidate, principal)) {
                    matched = true;
                    break;
                }
            }
            if (matched) {
                if (rule.deny()) {
                    throw new SecurityException("Denied by rule");
                }
                allowed = true;
            }
        }
        if (!allowed) {
            throw new SecurityException("Not allowed by policy");
        }
    }

    private static List<String> resourceHierarchy(String resourcePath) {
        String[] parts = resourcePath.split("/", -1);
        if (parts.length < 4 || !parts[0].equals("projects") || !isValidSegment(parts[1])
                || !parts[2].equals("databases") || !isValidSegment(parts[3])) {
            throw new IllegalArgumentException("Invalid Firestore resource: " + resourcePath);
        }
        List<String> list = new ArrayList<>();
        String project = "projects/" + parts[1];
        String db = project + "/databases/" + parts[3];
        list.add(project);
        list.add(db);
        StringBuilder cur = new StringBuilder(db);
        for (int i = 4; i < parts.length; i++) {
            cur.append('/').append(parts[i]);
            list.add(cur.toString());
        }
        return list;
    }

    private static void requireDatabasePath(String db) {
        if (db == null) {
            throw new IllegalArgumentException("Database is required");
        }
        String[] parts = db.split("/", -1);
        if (parts.length != 4 || !parts[0].equals("projects") || !isValidSegment(parts[1])
                || !parts[2].equals("databases") || !isValidSegment(parts[3])) {
            throw new IllegalArgumentException("Invalid database path");
        }
    }

    private static void requireParentPath(String parent) {
        if (parent == null) {
            throw new IllegalArgumentException("Parent is required");
        }
        String[] parts = parent.split("/", -1);
        if (parts.length < 5 || (parts.length - 5) % 2 != 0
                || !parts[0].equals("projects") || !isValidSegment(parts[1])
                || !parts[2].equals("databases") || !isValidSegment(parts[3])
                || !parts[4].equals("documents")) {
            throw new IllegalArgumentException("Invalid parent path");
        }
        for (int i = 5; i < parts.length; i++) {
            requireSingleSegment(parts[i]);
        }
    }

    private static void requireDocumentPath(String doc) {
        if (doc == null) {
            throw new IllegalArgumentException("Document name is required");
        }
        String[] parts = doc.split("/", -1);
        if (parts.length < 7 || (parts.length - 5) % 2 != 0
                || !parts[0].equals("projects") || !isValidSegment(parts[1])
                || !parts[2].equals("databases") || !isValidSegment(parts[3])
                || !parts[4].equals("documents")) {
            throw new IllegalArgumentException("Invalid document path");
        }
        for (int i = 5; i < parts.length; i++) {
            requireSingleSegment(parts[i]);
        }
    }

    private static void requireDocumentInDatabase(String db, String doc) {
        requireDatabasePath(db);
        requireDocumentPath(doc);
        if (!doc.startsWith(db + "/documents/")) {
            throw new IllegalArgumentException("Document does not belong to database");
        }
    }

    private static void requireSingleSegment(String seg) {
        if (!isValidSegment(seg)) {
            throw new IllegalArgumentException("Invalid path segment");
        }
    }

    private static boolean isValidSegment(String seg) {
        return seg != null && !seg.isBlank() && !seg.equals(".") && !seg.equals("..")
                && !seg.contains("/") && !seg.contains("\n") && !seg.contains("\r") && !seg.contains("\0");
    }

    private static void error(RoutingContext ctx, int code, String status, String message) {
        ctx.response().setStatusCode(code).putHeader("Content-Type", "application/json")
                .end(ErrorJson.render(code, status, message));
    }
}
