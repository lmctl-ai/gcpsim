# Pub/Sub ModifyAckDeadline overlay

Pinned upstream: floci-gcp 0.11.0, commit
`0fa563c603c1876a09b050ffdce0d96bac00fedc` (`upstream.lock.json`).

## Files copied into the overlay

`scripts/build.py` extracts the pinned upstream archive, then overlays only files
present under `src/`. This change adds three files under
`src/main/java/io/floci/gcp/services/pubsub/`:

1. `PubSubService.java` — full copy of the upstream file, one added method.
2. `PubSubSubscriberController.java` — full copy, gRPC handler now calls the
   service instead of returning success without doing anything.
3. `PubSubAckDeadlineRestController.java` — new 57-line JAX-RS resource for the
   missing REST route.

On an upstream bump, re-copy the first two files from the new pinned upstream
and re-apply the two small hunks below (3-way merge). The third file is ours and
normally needs no merge.

## Exact changes vs upstream

`PubSubService.java` — inserted `modifyAckDeadline(String, List<String>, int)`
immediately before `acknowledge`:

- missing subscription -> `GcpException.notFound("Subscription not found: ...")`
- `ackDeadlineSeconds < 0 || > 600` -> `INVALID_ARGUMENT`
- malformed ack ID (not a UUID issued by the simulator) -> `INVALID_ARGUMENT` ("Invalid ack ID"); well-formed stale/unknown ids -> accepted (200)
- deadline `0` removes the ack ID from the in-flight `delivered` map and appends
  the original message to the subscription queue, so the next pull redelivers it
- deadline `1..600` leaves the delivery in flight under the existing model

`PubSubSubscriberController.java` — `modifyAckDeadline` body now calls
`service.modifyAckDeadline(...)` and maps failures through
`GcpGrpcController.grpcError`; previously it returned `Empty` unconditionally.

`PubSubAckDeadlineRestController.java` — REST route
`POST /v1/projects/{project}/subscriptions/{subscription}:modifyAckDeadline`.
Validates body `ackIds` (non-empty strings) and `ackDeadlineSeconds` (0..600),
calls the service, returns `{}` / 200. The existing `PermissionFilter` already
maps this action to `pubsub.subscriptions.consume`.

## Why the 404 happened

Upstream had REST routes for `:pull` and `:acknowledge` but no route at all for
`:modifyAckDeadline`; the gRPC method existed but was a no-op. The overlay path
was not the cause.

## Verification

JVM build only (`python3 scripts/build.py`), GraalVM 25:

- `scripts/test-pubsub-ack.py`: PASS (0 redelivery, 30 s hide, 0..600 bound,
  invalid ackId INVALID_ARGUMENT, acknowledge)
- `scripts/test-cli.py`: PASS (14 checks)
- `scripts/test-lifecycle.py`: PASS
- `scripts/test-auth.py`: PASS (including enforced REST paths)
- Manual enforced REST modify: token-less create 401; authenticated
  topic/sub create 200; modify 0 200.
- lmcloud queue conformance against this JVM build: 13/13 (1 open-mode skip).
