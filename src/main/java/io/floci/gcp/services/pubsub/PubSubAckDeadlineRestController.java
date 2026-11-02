package io.floci.gcp.services.pubsub;

import io.floci.gcp.core.common.GcpException;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** REST ModifyAckDeadline, matching the Pub/Sub JSON API action path. */
@Path("/v1/projects/{project}")
@ApplicationScoped
@Produces(MediaType.APPLICATION_JSON)
@Consumes(MediaType.APPLICATION_JSON)
public class PubSubAckDeadlineRestController {
    private final PubSubService service;

    @Inject
    public PubSubAckDeadlineRestController(PubSubService service) {
        this.service = service;
    }

    @POST
    @Path("/subscriptions/{subscription}:modifyAckDeadline")
    public Response modify(@PathParam("project") String project,
                           @PathParam("subscription") String subscription,
                           Map<String, Object> body) {
        Object rawIds = body == null ? null : body.get("ackIds");
        if (!(rawIds instanceof List<?> list) || list.isEmpty()) {
            throw GcpException.invalidArgument("ackIds must be a non-empty list");
        }
        List<String> ackIds = new ArrayList<>(list.size());
        for (Object value : list) {
            if (!(value instanceof String text) || text.isBlank()) {
                throw GcpException.invalidArgument("ackIds must contain non-empty strings");
            }
            ackIds.add(text);
        }
        Object rawSeconds = body.get("ackDeadlineSeconds");
        if (!(rawSeconds instanceof Number number)) {
            throw GcpException.invalidArgument("ackDeadlineSeconds must be an integer");
        }
        int seconds = number.intValue();
        if (!(seconds == 0 || (seconds >= 10 && seconds <= 600))) {
            throw GcpException.invalidArgument("ackDeadlineSeconds must be 0 or 10..600");
        }
        service.modifyAckDeadline("projects/" + project + "/subscriptions/" + subscription, ackIds, seconds);
        return Response.ok(Map.of()).build();
    }
}
