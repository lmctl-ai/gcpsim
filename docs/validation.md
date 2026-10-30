# Validation record

All five platforms passed in [CI run 40978406438](https://github.com/lmctl-ai/gcpsim/actions/runs/40978406438),
for commit `d983f408cb2022aeee6f582050cc73a39e8dbdd5` (tag `v0.1.0`).
Completed 2026-10-30 UTC, 2026-10-29 America/Chicago.

| Platform | JVM suite | Native suite |
|---|---|---|
| Linux x64 | PASS | PASS |
| Linux arm64 | PASS | PASS |
| macOS x64 | PASS | PASS |
| macOS arm64 | PASS | PASS |
| Windows x64 | PASS | PASS |

The integration gate runs against both the JVM distribution and standalone
native executable. It uses gcloud 543.0.0 with isolated configuration, fake
access tokens, explicit loopback endpoints, and no real GCP credentials.

Checks:

- 12 CLI checks: version, UTF-8 help, invalid commands and invalid options.
- Occupied port returns exit 3 and a JSON error envelope.
- `start` alias works; a persistent bucket survives a process restart.
- GCS bucket creation, binary upload, byte-exact download, and deletion.
- Pub/Sub topic/subscription creation, publish, pull, acknowledgement, and deletion.
- Owned emulator process is stopped after the test, including failure paths.

Local Linux ARM64 verification additionally downloaded a CI-built executable,
ran it from an isolated temporary working directory, and passed the same checks.
The executable is an ELF binary; the test launches it directly without a JVM.

## Packaging issues resolved

- 4 GiB native-build heap was insufficient: analysis spent minutes in full GC.
  With 6 GiB, Linux x64 compilation completed in 4m24s, with peak RSS 7.23 GB.
  Allow RAM beyond the Java heap for compiler and Maven overhead. The standard
  macOS arm64 runner needed 25m24s for a cold native build. The native cache
  avoids recompiling unchanged inputs, while the test suite always runs again.
- Windows gcloud's path globbing missed `RUNNER~1` short directory names.
  The harness uses an isolated working directory and relative payload names.
- Windows native compilation must defer docker-java's Unix `DomainSocket`
  initialization. The appended flag preserves upstream's other native flags.
- Overlay source reading and captured process output must explicitly use UTF-8
  on Windows; relying on the system code page corrupts help text.

This is a REST GCS/Pub/Sub compatibility gate, not a full GCP conformance suite.
Upstream gRPC services and Docker-backed data planes are not independently
validated here. lmgcp external-endpoint integration is deferred until that host
supports an external emulator rather than only its own fake or real GCP.

## Release verification

[Release run 40981605819](https://github.com/lmctl-ai/gcpsim/actions/runs/40981605819)
restored the native caches and reran both test tiers on all five platforms before
publishing [v0.1.0](https://github.com/lmctl-ai/gcpsim/releases/tag/v0.1.0).
The published Linux ARM64 executable was downloaded again, verified against the
published `SHA256SUMS`, and executed successfully with `--version`.

## Wave 11 JVM validation

Local Java 25 validation passed CLI (14 cases), lifecycle/persistence/occupied
port checks, and the existing gcloud GCS/Pub/Sub suite. The new auth suite passed:
malformed startup fixtures; missing/wrong/malformed bearer credentials; statement
allow and deny; explicit deny overriding statements and upstream IAM bindings;
wildcard action/resource matching; empty policy default deny; auth-only mode;
unsupported REST/gRPC transport rejection; and no token leakage.

A source-built lmgcp from the Wave 11 client changes completed GCS bucket creation,
SDK multipart upload, byte-exact download, topic/subscription creation, publish and
pull against the gcpsim JVM process. Independent REST reads found the client-created
object and topic. The CLI returned application failure for both 401 and 403.
Release-binary and native validation will be recorded after CI completes.

The final local client run used source-built lmgcp v0.1.9. Its full existing suite
passed all 58 checks, including 40 numbered examples, account inventory/plotting,
schema parity, memory limits, and comparison fixtures. The unnumbered inventory
example had two pre-existing CI problems (CSV delimiter and cost-lab assumptions);
these were corrected before the replacement release tag.

The CI-built Linux ARM64 native artifact from run `40992492411` also passed the
complete lmgcp integration and auth suite locally. CLI authentication and permission
failures use exit **4** with JSON `APP_FAIL`; success uses exit 0.

Pinned client release: [lmgcp v0.1.9](https://github.com/lmctl-ai/lmgcp/releases/tag/v0.1.9),
source `db4f5193ce8258b2ef16e035743da9f7fc4816a6`. Its [release workflow](https://github.com/lmctl-ai/lmgcp/actions/runs/40992609393)
passed all five platforms. Release SHA-256 values are committed in `client.lock.json`.
A final vendor-table review corrected Pub/Sub create checks to use the containing
project; the tests now require an exact project match and deny cross-project creation.
The earlier gcpsim native run was canceled so the corrected source can be rebuilt.

Additional authorization regressions reproduced failures in the earlier native
artifact before fixes: an object name containing a newline bypassed wildcard Deny
when a binding allowed creation, and metadata in a later multipart part could
misdirect the authorization check. The overlay now uses newline-aware globs and
reads the first multipart metadata part, including upstream's absent/null-name
query fallback. These cases are included in every JVM/native auth test run.

## Wave 11 final implementation: v0.2.0

Source commit `3efc4dd770d7f27d3fa45e742de226e765b046e2` passed the complete
[main workflow](https://github.com/lmctl-ai/gcpsim/actions/runs/40995924220).

| Platform | JVM CLI, lifecycle, gcloud, auth + lmgcp | Native CLI, lifecycle, gcloud, auth + lmgcp |
| --- | --- | --- |
| Linux x64 | PASS | PASS |
| Linux ARM64 | PASS | PASS |
| macOS x64 | PASS | PASS |
| macOS ARM64 | PASS | PASS |
| Windows x64 | PASS | PASS |

The auth matrix passed on all five platforms, for both JVM and native builds:

| Case | Result |
| --- | --- |
| Missing, malformed, and unknown bearer tokens on GCS/Pub/Sub | 401 UNAUTHENTICATED |
| Configured token with an applicable allow statement/binding | Success |
| Principal mismatch, unmatched resources, empty policies | 403 PERMISSION_DENIED |
| Explicit Deny vs statement allow and upstream binding allow | Deny wins |
| Wildcard actions/resources, including newline-containing object names | PASS |
| First multipart metadata part and null-name query fallback | Correct resource enforced |
| Pub/Sub create on containing project and cross-project denial | PASS |
| Unsupported services/transports and IAM mutations in enforce mode | Rejected |
| Malformed startup configuration | Exit 2; no token echo |
| lmgcp authentication/permission failure | Exit 4, JSON APP_FAIL; no token leakage |
| No policy section vs explicitly empty policy section | Auth-only allow vs default deny |

Integration evidence: the pinned **lmgcp v0.1.9 release binary** created a bucket,
uploaded `hello.txt` through the GCS SDK, and downloaded the exact
`gcpsim-roundtrip` payload. It created a topic and subscription, published
`gcpsim-message`, and pulled the same message. Independent simulator REST reads
found the client-created object and topic. Every test owns a fresh emulator
process, so the client's internal fake cannot satisfy those checks.

The Linux ARM64 native binary also passed this suite locally with the downloaded,
checksum-verified client. Its SHA-256 is
`c5f131260e2f19d7f74f338680360be747e38f70f9c04b26fd023e246d756c15`.
The macOS ARM64 cold native build took 30m38s; its native tests passed in 16s.
See [auth configuration and limits](auth.md) for the finite REST mapping and
deferred full-cloud IAM/OAuth behavior.

Published [gcpsim v0.2.0](https://github.com/lmctl-ai/gcpsim/releases/tag/v0.2.0)
on 2026-10-30 at 04:11:10 UTC. The [release workflow](https://github.com/lmctl-ai/gcpsim/actions/runs/40999994470)
reused the exact native caches, reran the complete JVM/native suites on all five
platforms, and published five binaries, both license files, both provenance locks,
and `SHA256SUMS` (10 assets). A fresh download of the published Linux ARM64 binary
and `client.lock.json` passed checksum verification and matched the tested local
artifacts byte-for-byte. The binary reports `gcpsim 0.2.0 (floci-gcp 0.11.0)`.
