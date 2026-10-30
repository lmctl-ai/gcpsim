# Client bytecode fixtures

`roundtrip.dslb` creates a bucket, uploads and reads an object, creates a topic and
subscription, publishes a message, and checks the pulled payload. `denied.dslb`
requests a bucket so the process-level test can assert 401/403 exit behavior.
Resources live in disposable simulator processes; names use lmgcp's required prefix.

Bytecode is committed so CI runs the pinned client binary without installing a
compiler or silently substituting the client's fake. Sources are the adjacent
`.lms` files. They were compiled with `@lmctl-ai/lmctl@0.2.420` (DSL v5) and lmgcp's
unchanged host schema. To regenerate, put the pinned lmgcp binary on PATH, run
`lmscript tests/fixtures/roundtrip.lms` and `lmscript tests/fixtures/denied.lms` from
the repository root, then move the resulting `.dslb` files into this directory.
The round trip verifies payload content; the Python runner independently reads
client-created resources through the simulator endpoint.
