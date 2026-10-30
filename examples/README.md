# Template-driven client integration

`templates.lms` constructs GCS bucket labels, JSON object content, Pub/Sub message
attributes, and an IAM/auth configuration using multiline `_json_` blocks. The
context includes quotes, Unicode, backslashes, numeric/list values and a literal
`${untouched}`. `expand()` constructs the permission resource path; JSON is
never assembled with string expansion.

Run from the repository root after building gcpsim and downloading the pinned
client (`python3 scripts/download-client.py`):

```sh
npm install --prefix .cache/templates @lmctl-ai/lmctl@0.2.444
npm install --prefix .cache/client-compiler @lmctl-ai/lmctl@0.2.420
LMSCRIPT_BIN="$PWD/.cache/templates/node_modules/@lmctl-ai/lmctl/bin/lmscript" \
LMGCP_COMPILER="$PWD/.cache/client-compiler/node_modules/@lmctl-ai/lmctl/bin/lmscript" \
python3 scripts/test-templates.py dist/gcpsim dist/lmgcp
```

For the mono engine, set `LMSCRIPT_BIN=$HOME/lmctl-mono/bin/lmscript` instead.
For JVM testing, replace `dist/gcpsim` with `dist/quarkus-app/quarkus-run.jar` and
set `JAVA` if needed. Windows uses the `.exe` simulator/client paths; the harness
invokes compiler JavaScript entrypoints with Node, avoiding shell wrappers.

The harness owns two temporary servers: open mode without credentials, then
bearer-enforced mode using the generated policy. It invokes the real lmgcp client
and independently asserts HTTP statuses, bucket labels, object metadata/content,
and published message data/attributes. The project storage binding permits the
round trip; explicit deny overrides it for deletion and a separate bucket read.
Both REST vendor-shaped 401/403 responses and client exit 4/APP_FAIL are checked.
Every failure exits nonzero; temporary files and servers are cleaned up.

## Compiler compatibility

The released, checksum-pinned lmgcp v0.1.9 reads bytecode v5. Current engines emit
v6. `templates-client.lms` is a small v5 adapter compiled by isolated 0.2.420:
it reads the generated JSON context and splices its values into typed host calls.
The current engine constructs all templates, writes the IAM file and orchestrates
execution. This avoids changing the pinned client, its v5 drawing dependency, or
the user's global tools. The adapter uses the same `_dsl_ cli="lmgcp"` schema
discovery convention as the Wave 11 fixtures. No precompiled artifact is trusted.

The IAM configuration is a local emulator fixture, not a production GCP policy.
Only dummy bearer credentials are used. See [auth scope](../docs/auth.md).
