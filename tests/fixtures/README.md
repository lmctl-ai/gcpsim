# lmcode source fixtures

`roundtrip.lms` creates a bucket, uploads and reads an object, creates a topic and
subscription, publishes a message, and checks the pulled payload through
`run_lmcode`. `denied.lms` requests a bucket so the auth tests can inspect the
controlled invocation failure. Resources live in disposable simulator processes;
names use lmgcp's required prefix.

The scripts run through the current lmscript command builder. They do not write
bytecode files; the `run_lmcode` result is the fixture's output. The pinned
lmgcp release used by CI must expose the current invocation manifest and
terminal result frame protocol before these host calls run.
