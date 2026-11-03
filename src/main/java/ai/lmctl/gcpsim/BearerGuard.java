package ai.lmctl.gcpsim;

import io.floci.gcp.services.iam.IamPolicyEvaluator;
import io.floci.gcp.services.iam.IamPrincipal;
import io.floci.gcp.services.iam.IamResource;
import jakarta.enterprise.event.Observes;
import jakarta.annotation.Priority;
import jakarta.inject.Inject;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.RoutingContext;
import jakarta.enterprise.context.ApplicationScoped;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Runs before REST and gRPC routing; alternate transports cannot bypass enforcement. */
@ApplicationScoped
public class BearerGuard {
    private static final Pattern BEARER = Pattern.compile("(?i:Bearer) ([A-Za-z0-9._~+/-]+=*)");
    private static final String FIRESTORE_PREFIX = "/google.firestore.v1.Firestore/";

    @Inject
    IamPolicyEvaluator evaluator;

    void init(@Observes @Priority(1) Router router) { router.route().order(Integer.MIN_VALUE).handler(this::guard); }

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
            String action = firestoreAction(path.substring(FIRESTORE_PREFIX.length()));
            if (action == null) {
                error(ctx, 403, "PERMISSION_DENIED", "Operation is outside the enforced Firestore gRPC subset.");
                return;
            }
            String principal = AuthConfig.tokens.get(bearer.group(1));
            String project = System.getProperty("floci-gcp.default-project-id", "gcpsim-local");
            IamResource resource = IamResource.project("projects/" + project);
            boolean allowed = !AuthConfig.hasPolicies
                    || (evaluator != null && evaluator.isAllowed(new IamPrincipal(principal), action, resource, AuthConfig.bindings));
            for (AuthConfig.Rule rule : AuthConfig.rules) {
                if (rule.matches(action, resource.name(), principal)) {
                    if (rule.deny()) {
                        error(ctx, 403, "PERMISSION_DENIED", "Request is not permitted by the emulator auth configuration.");
                        return;
                    }
                    allowed = true;
                }
            }
            if (!allowed) {
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

    private static String firestoreAction(String method) {
        return switch (method) {
            case "CreateDocument" -> "datastore.entities.create";
            case "GetDocument", "BatchGetDocuments", "BeginTransaction" -> "datastore.entities.get";
            case "UpdateDocument", "Commit", "Rollback", "BatchWrite", "Write" -> "datastore.entities.update";
            case "DeleteDocument" -> "datastore.entities.delete";
            case "ListDocuments", "RunQuery", "RunAggregationQuery", "PartitionQuery", "Listen" -> "datastore.entities.list";
            case "ListCollectionIds" -> "datastore.databases.listCollectionIds";
            default -> null;
        };
    }

    private static void error(RoutingContext ctx, int code, String status, String message) {
        ctx.response().setStatusCode(code).putHeader("Content-Type", "application/json")
                .end(ErrorJson.render(code, status, message));
    }
}
