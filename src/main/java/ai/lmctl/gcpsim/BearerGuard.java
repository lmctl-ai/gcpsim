package ai.lmctl.gcpsim;

import jakarta.enterprise.event.Observes;
import jakarta.annotation.Priority;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.RoutingContext;
import jakarta.enterprise.context.ApplicationScoped;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Runs before REST routing; alternate transports cannot bypass enforcement. */
@ApplicationScoped
public class BearerGuard {
    private static final Pattern BEARER = Pattern.compile("(?i:Bearer) ([A-Za-z0-9._~+/-]+=*)");

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
        if (!(path.startsWith("/storage/v1/") || path.startsWith("/upload/storage/v1/")
                || path.startsWith("/download/storage/v1/") || path.startsWith("/v1/projects/"))) {
            error(ctx, 403, "PERMISSION_DENIED", "Operation is outside the enforced REST subset.");
            return;
        }
        ctx.next();
    }
    private static void error(RoutingContext ctx, int code, String status, String message) {
        ctx.response().setStatusCode(code).putHeader("Content-Type", "application/json")
                .end(ErrorJson.render(code, status, message));
    }
}
