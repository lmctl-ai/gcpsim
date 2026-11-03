# Template-driven client integration

`templates.lms` constructs GCS bucket labels, JSON object content, Pub/Sub message
attributes, and an IAM/auth configuration using multiline `_json_` blocks. The
context includes quotes, Unicode, backslashes, numeric/list values and a literal
`${untouched}`. `expand()` constructs the permission resource path; JSON is
never assembled with string expansion.

Run from the repository root after building gcpsim and downloading the pinned
client (`python3 scripts/download-client.py`):

```sh
npm install --prefix .cache/templates --no-save --package-lock=false @lmctl-ai/lmctl@latest
LMSCRIPT_BIN="$PWD/.cache/templates/node_modules/@lmctl-ai/lmctl/bin/lmscript" \
python3 scripts/test-templates.py dist/gcpsim dist/lmgcp
```

For the mono engine, set `LMSCRIPT_BIN=$HOME/lmctl-mono/bin/lmscript` instead.
For JVM testing, replace `dist/gcpsim` with `dist/quarkus-app/quarkus-run.jar` and
set `JAVA` if needed. Windows uses the `.exe` simulator/client paths; the harness
invokes compiler JavaScript entrypoints with Node, avoiding shell wrappers.

The harness owns two temporary servers: open mode without credentials, then
bearer-enforced mode using the generated policy. The pinned lmgcp release supports invocation manifests. The examples execute
through `run_lmcode`, and the harness independently asserts HTTP statuses, bucket labels, object
metadata/content, and published message data/attributes. The project storage
binding permits the round trip; explicit deny overrides it for deletion and a
separate bucket read. The harness fails if the pinned client lacks the protocol
or any resource assertion fails. Temporary files and servers are cleaned up.

## lmcode invocation

`templates-client.lms` reads the generated JSON fixture, constructs one typed
lmcode program, and executes it once through `run_lmcode`. It writes no bytecode
artifact. The SDK install is pinned to lmgcp v0.1.17.

The IAM configuration is a local emulator fixture, not a production GCP policy.
Only dummy bearer credentials are used. See [auth scope](../docs/auth.md).
