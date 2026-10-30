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
