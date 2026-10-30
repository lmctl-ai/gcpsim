package ai.lmctl.gcpsim;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.floci.gcp.services.iam.IamPolicyEvaluator;
import io.floci.gcp.services.iam.IamPrincipal;
import io.floci.gcp.services.iam.IamResource;
import jakarta.annotation.Priority;
import jakarta.inject.Inject;
import jakarta.ws.rs.Priorities;
import jakarta.ws.rs.container.ContainerRequestContext;
import jakarta.ws.rs.container.ContainerRequestFilter;
import jakarta.ws.rs.core.Response;
import jakarta.ws.rs.ext.Provider;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Strict, finite HTTP operation mapping. Unknown operations fail closed in enforce mode. */
@Provider
@Priority(Priorities.AUTHORIZATION)
public class PermissionFilter implements ContainerRequestFilter {
    private final IamPolicyEvaluator evaluator;
    private final ObjectMapper json;
    @Inject
    public PermissionFilter(IamPolicyEvaluator evaluator, ObjectMapper json) {
        this.evaluator = evaluator;
        this.json = json;
    }
    @Override
    public void filter(ContainerRequestContext ctx) {
        if (!AuthConfig.enforce || (ctx.getUriInfo().getPath().replaceFirst("^/+", "").equals("health") && ctx.getMethod().equals("GET"))) { return; }
        try {
            String header = ctx.getHeaderString("Authorization");
            String principal = header == null || header.length() < 8 ? null : AuthConfig.tokens.get(header.substring(7));
            if (principal == null) { reject(ctx, 401, "UNAUTHENTICATED"); return; }
            List<Check> checks = map(ctx);
            for (Check check : checks) {
                boolean allowed = !AuthConfig.hasPolicies || evaluator.isAllowed(new IamPrincipal(principal), check.action(), check.resource(), AuthConfig.bindings);
                for (AuthConfig.Rule rule : AuthConfig.rules) {
                    if (rule.matches(check.action(), check.resource().name(), principal)) {
                        if (rule.deny()) { reject(ctx, 403, "PERMISSION_DENIED"); return; }
                        allowed = true;
                    }
                }
                if (!allowed) { reject(ctx, 403, "PERMISSION_DENIED"); return; }
            }
        } catch (Exception e) {
            reject(ctx, 403, "PERMISSION_DENIED");
        }
    }
    private List<Check> map(ContainerRequestContext ctx) throws Exception {
        String path = "/" + ctx.getUriInfo().getPath().replaceFirst("^/+", "");
        String method = ctx.getMethod();
        String project = System.getProperty("floci-gcp.default-project-id", "gcpsim-local");
        String storagePath = path.replaceFirst("^/(upload|download)/storage/", "/storage/");
        if (storagePath.equals("/storage/v1/b")) {
            String requested = ctx.getUriInfo().getQueryParameters().getFirst("project");
            return List.of(new Check("storage.buckets." + operation(method, "GET", "list", "POST", "create"),
                    IamResource.project("projects/" + (requested == null ? project : requested))));
        }
        if (storagePath.startsWith("/storage/v1/b/")) {
            String[] parts = storagePath.substring(14).split("/", 3);
            String bucket = parts[0];
            if (parts.length == 1) {
                return List.of(new Check("storage.buckets." + operation(method, "GET", "get", "DELETE", "delete", "PATCH", "update", "PUT", "update"), IamResource.gcsBucket(bucket, project)));
            }
            if (!parts[1].equals("o")) { throw new IllegalArgumentException(); }
            if (parts.length == 2 && method.equals("GET")) {
                return List.of(new Check("storage.objects.list", IamResource.gcsBucket(bucket, project)));
            }
            if (parts.length == 2 && method.equals("POST") && path.startsWith("/upload/")) {
                if (ctx.getUriInfo().getQueryParameters().containsKey("upload_id")) { throw new IllegalArgumentException(); }
                String upload = ctx.getUriInfo().getQueryParameters().getFirst("uploadType");
                String name = ctx.getUriInfo().getQueryParameters().getFirst("name");
                if ("multipart".equals(upload)) {
                    String body = new String(body(ctx), StandardCharsets.UTF_8);
                    int type = body.toLowerCase(java.util.Locale.ROOT).indexOf("content-type: application/json");
                    int begin = body.indexOf("\r\n\r\n", type);
                    int end = body.indexOf("\r\n--", begin + 4);
                    if (type < 0 || begin < 0 || end < 0) { throw new IllegalArgumentException(); }
                    name = json.readTree(body.substring(begin + 4, end)).path("name").asText();
                } else if (!"media".equals(upload)) { throw new IllegalArgumentException(); }
                if (name == null || name.isBlank()) { throw new IllegalArgumentException(); }
                return List.of(new Check("storage.objects.create", IamResource.gcsObject(bucket, name, project)));
            }
            if (parts.length == 3) {
                return List.of(new Check("storage.objects." + operation(method, "GET", "get", "DELETE", "delete", "PATCH", "update", "PUT", "update"), IamResource.gcsObject(bucket, parts[2], project)));
            }
        }
        if (path.matches("/v1/projects/[^/]+/(topics|subscriptions)(/[^/]+)?")) {
            String resourceName = path.substring(4);
            String[] parts = resourceName.split("/");
            String kind = parts[2];
            String action;
            if (parts.length == 3) {
                action = operation(method, "GET", "list");
                resourceName = "projects/" + parts[1];
            } else if (parts[3].contains(":")) {
                String[] suffix = parts[3].split(":", -1);
                if (suffix.length != 2 || !method.equals("POST")) { throw new IllegalArgumentException(); }
                action = kind.equals("topics") ? operation(suffix[1], "publish", "publish")
                        : operation(suffix[1], "pull", "consume", "acknowledge", "consume", "modifyAckDeadline", "consume");
                resourceName = resourceName.substring(0, resourceName.indexOf(':'));
            } else { action = operation(method, "GET", "get", "PUT", "create", "DELETE", "delete"); }
            List<Check> checks = new ArrayList<>();
            String checkedResource = action.equals("create") ? "projects/" + parts[1] : resourceName;
            checks.add(new Check("pubsub." + kind + "." + action, IamResource.projectChild("pubsub.googleapis.com", kind.equals("topics") ? "Topic" : "Subscription", checkedResource, checkedResource)));
            if (kind.equals("subscriptions") && action.equals("create")) {
                JsonNode request = json.readTree(body(ctx));
                String topic = request.path("topic").asText();
                if (!topic.matches("projects/[^/]+/topics/[^/:]+")) { throw new IllegalArgumentException(); }
                checks.add(new Check("pubsub.topics.attachSubscription", IamResource.projectChild("pubsub.googleapis.com", "Topic", topic, topic)));
            }
            return checks;
        }
        throw new IllegalArgumentException();
    }
    private static byte[] body(ContainerRequestContext ctx) throws Exception {
        byte[] bytes = ctx.getEntityStream().readNBytes(16 * 1024 * 1024 + 1);
        if (bytes.length > 16 * 1024 * 1024) { throw new IllegalArgumentException(); }
        ctx.setEntityStream(new ByteArrayInputStream(bytes));
        return bytes;
    }
    private static String operation(String value, String... pairs) {
        for (int i = 0; i < pairs.length; i += 2) { if (value.equals(pairs[i])) { return pairs[i + 1]; } }
        throw new IllegalArgumentException();
    }
    private static void reject(ContainerRequestContext ctx, int code, String status) {
        ctx.abortWith(Response.status(code).type("application/json").entity(Map.of("error", Map.of("code", code, "status", status, "message", "Request is not permitted by the emulator auth configuration."))).build());
    }
    record Check(String action, IamResource resource) {}
}
