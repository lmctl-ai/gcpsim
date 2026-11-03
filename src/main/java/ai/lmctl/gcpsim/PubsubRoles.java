package ai.lmctl.gcpsim;
import com.fasterxml.jackson.databind.JsonNode;
import io.floci.gcp.services.iam.IamResource;
import io.floci.gcp.services.iam.authorization.IamAuthorizationAdapter;
import io.floci.gcp.services.iam.authorization.IamOperation;
import io.floci.gcp.services.iam.authorization.IamPermissionCheck;
import jakarta.enterprise.context.ApplicationScoped;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/** Adds finite Pub/Sub and Firestore/Datastore roles to the upstream IAM evaluator. */
@ApplicationScoped
public class PubsubRoles implements IamAuthorizationAdapter {
    public String serviceName() { return "gcpsim-pubsub"; }
    public Set<Class<?>> restControllers() { return Set.of(); }
    public Set<String> grpcServices() { return Set.of(); }
    public IamOperation restOperation(String method, Map<String,String> path, JsonNode body) { throw unsupported(method); }
    public IamOperation grpcOperation(String method, JsonNode body) { throw unsupported(method); }
    public List<IamPermissionCheck> checks(IamOperation operation) { throw unsupported(operation.name()); }
    public Optional<IamResource> resource(String name) { return Optional.empty(); }
    public Map<String, Set<String>> roles() {
        return Map.of(
            "roles/pubsub.publisher", Set.of("pubsub.topics.publish"),
            "roles/pubsub.subscriber", Set.of("pubsub.subscriptions.consume", "pubsub.topics.attachSubscription"),
            "roles/pubsub.viewer", Set.of("pubsub.topics.get", "pubsub.topics.list", "pubsub.subscriptions.get", "pubsub.subscriptions.list"),
            "roles/datastore.user", Set.of("datastore.entities.create", "datastore.entities.get", "datastore.entities.update", "datastore.entities.delete", "datastore.entities.list", "datastore.databases.get", "datastore.databases.listCollectionIds"),
            "roles/datastore.viewer", Set.of("datastore.entities.get", "datastore.entities.list", "datastore.databases.get", "datastore.databases.listCollectionIds")
        );
    }
}
