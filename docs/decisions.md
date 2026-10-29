# Packaging decisions

- Build floci-gcp 0.11.0 from immutable commit plus SHA256-verified archive.
  Its GitHub release has no binary assets. Keep only a CLI overlay here; do not
  fork-mirror upstream or rewrite its protocol implementations.
- Native Quarkus build includes the emulator itself. No download-on-first-run,
  launcher subprocess, container, or runtime JVM required.
- Default to loopback and in-memory state. Docker-backed services use mock mode
  unless explicitly configured otherwise. GCS and Pub/Sub need no Docker.
- Test with real gcloud (543.0.0), using isolated configuration and local endpoint
  overrides with fake tokens. Inspected lmgcp commit
  `3cb738debb18cb9e042b80adb87b514b492b9621`: NewHost supports only an internal fake
  server or authenticated real GCP, with no external emulator endpoint option.
  Therefore use the assignment's gcloud fallback rather than altering another
  project's safety and client initialization semantics.
- Build and test on five platforms in CI. Only publish a tag after all matrix
  jobs pass. Upstream's broad service surface is not a claim of complete GCP
  parity: the release gate here covers CLI, GCS and Pub/Sub only.
- Preserve upstream MIT license. Third-party Maven dependencies retain their
  own licenses; consult the pinned upstream POM for the dependency inventory.
