# Local authentication and permissions

Default mode is **open**. To enforce the supported REST subset:

```
gcpsim serve --auth enforce --auth-config auth.json
# Equivalent: SIM_AUTH=enforce SIM_AUTH_CONFIG=auth.json gcpsim serve
```

`auth.json` is a startup-only fixture, containing dummy tokens, optional statements,
and optional resource IAM bindings. Configuration errors exit 2 without quoting
values. Files are read before the listener starts; changing a file requires restart.

```json
{
  "tokens": {"local-test-token": "serviceAccount:tester@example.test"},
  "Statement": [
    {"Effect": "Allow", "ActionGlob": "storage.*", "ResourceGlob": "projects/*"},
    {"Effect": "Deny", "ActionGlob": "*.delete", "ResourceGlob": "*"}
  ],
  "policies": {
    "projects/gcpsim-local/topics/events": {
      "bindings": [{"role": "roles/pubsub.publisher", "members": ["serviceAccount:tester@example.test"]}]
    },
    "buckets/example": {
      "bindings": [{"role": "roles/storage.objectViewer", "members": ["allAuthenticatedUsers"]}]
    }
  }
}
```

Tokens map to `user:` or `serviceAccount:` members. Exactly one syntactically valid
Bearer header must match a configured token. Missing/malformed/unknown tokens get
HTTP 401 with a JSON `error` containing `code: 401`, `status: UNAUTHENTICATED`, and
a generic message. Permission failures return 403 / `PERMISSION_DENIED`. Tokens
are never echoed. `x-goog-user-project` passes through and does not grant access.
GET `/health` is intentionally public; other routes require authentication.

With neither `Statement` nor `policies`, authenticated mapped operations are
allowed. Presence of either, even empty, enables default deny. Any matching Deny
wins over statements **and** bindings. Statements take scalar `Action` or
`ActionGlob`, `ResourceGlob`, and optional `Principal`; `*` matches any sequence,
`?` one character. These are emulator rules, not Google IAM deny-policy JSON.
Unknown fields, malformed rules and unsupported binding conditions fail startup.
Unknown roles grant nothing. Bindings reuse upstream `IamPolicyEvaluator`, role
catalog, member matching, and resource hierarchy; the overlay contributes Pub/Sub
publisher/subscriber/viewer roles. Upstream token issuance and anonymous GCS IAM
interceptors are not used to validate configured dummy tokens.

| REST operation | Permission | Statement resource name |
| --- | --- | --- |
| GCS bucket list/create | storage.buckets.list/create | projects/PROJECT |
| GCS bucket get/update/delete | storage.buckets.get/update/delete | projects/_/buckets/BUCKET |
| GCS object list | storage.objects.list | projects/_/buckets/BUCKET |
| GCS object get/update/delete | storage.objects.get/update/delete | projects/_/buckets/BUCKET/objects/OBJECT |
| GCS media/multipart upload | storage.objects.create | projects/_/buckets/BUCKET/objects/OBJECT |
| Pub/Sub topic/subscription list | pubsub.topics.list / pubsub.subscriptions.list | projects/PROJECT |
| Pub/Sub topic/subscription create/get/delete | pubsub.topics.* / pubsub.subscriptions.* | projects/PROJECT/topics/NAME or subscriptions/NAME |
| Publish | pubsub.topics.publish | projects/PROJECT/topics/NAME |
| Pull/acknowledge/modifyAckDeadline | pubsub.subscriptions.consume | projects/PROJECT/subscriptions/NAME |
| Subscription create (additional check) | pubsub.topics.attachSubscription | referenced topic |

Binding keys are `buckets/BUCKET`, Pub/Sub resource names, or `projects/PROJECT`.
Object checks inherit bucket bindings. GCS named-resource project ancestry uses
the configured emulator project (single-project fixture model).

Enforcement is an HTTP-boundary overlay; upstream source is not modified. Other
services, IAM policy mutation, XML, resumable uploads, batch, and unmapped methods
are rejected in enforce mode. Multipart requests are buffered to a 16 MiB limit
for object-name authorization; media upload names come from the query. This is a
local testing subset: no JWT signature verification, ADC, token exchange, expiry,
CEL conditions, Google organization hierarchy, ACL enforcement, or production
OAuth security is claimed. Open mode retains upstream behavior.

References: [GCP API errors](https://cloud.google.com/apis/design/errors),
[GCS IAM roles](https://cloud.google.com/storage/docs/access-control/iam-roles),
[Pub/Sub access control](https://cloud.google.com/pubsub/docs/access-control).

# Durable cross-cloud lessons

- Keep credential validation separate from policy evaluation and default to open.
- Reuse vendor-shaped errors and existing role engines, but audit transport bypasses.
- Empty policies must deny; absent policies may allow authenticated requests.
- Bindings are allow-only. Apply explicit denies before returning an allow result.
- Pin both client and compiler. An internal fake can make a broken integration
  appear green: read client-created resources independently from the simulator.
- Auth errors must survive subsequent successful client calls and must never echo
  configured tokens. Test process exit status as well as response bodies.
- Native caching must include every overlay source file. Re-run auth and integration
  checks on cache hits before publishing.
