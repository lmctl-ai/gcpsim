# gcpsim

A local GCP emulator built on [floci-gcp](https://github.com/floci-io/floci-gcp),
packaged as one native executable. REST and gRPC share port **4588**. No GCP
account, Java installation, or Docker is needed to run the native binary for
GCS and Pub/Sub.

## Install

Download a binary and `SHA256SUMS` from the [releases](https://github.com/lmctl-ai/gcpsim/releases).
For Linux x64:

```sh
gh release download v0.1.0 --repo lmctl-ai/gcpsim --pattern gcpsim-linux-x64 --pattern SHA256SUMS
grep ' gcpsim-linux-x64$' SHA256SUMS | sha256sum -c -
chmod +x gcpsim-linux-x64
./gcpsim-linux-x64 serve
```

Other assets are `gcpsim-linux-arm64`, `gcpsim-macos-x64`,
`gcpsim-macos-arm64`, and `gcpsim-windows-x64.exe`. You can rename the executable
to `gcpsim` (`gcpsim.exe` on Windows). Binaries use GraalVM's default CPU targets:
x86-64-v3 or ARMv8.1-A. No runtime JVM installation is required.

## Build and run

Requires Python 3.12+, Maven 3.9+, and GraalVM JDK 25 with `native-image` on PATH.

```sh
python3 scripts/build.py --native
./dist/gcpsim --version
./dist/gcpsim serve
```

`start` is an alias for `serve`; both run in the foreground. Stop with Ctrl-C.
Default binding is `127.0.0.1:4588`, project `gcpsim-local`, in-memory state.
Use `--port 4590`, `--project my-project`, or `--data-dir ./data` for durable
state. `--host 0.0.0.0` explicitly permits network access to this unauthenticated
emulator. Each persistent instance needs its own directory.

```sh
export GOOGLE_CLOUD_PROJECT=gcpsim-local
export STORAGE_EMULATOR_HOST=http://127.0.0.1:4588
export PUBSUB_EMULATOR_HOST=127.0.0.1:4588
curl http://127.0.0.1:4588/health
```

Upstream configuration uses `FLOCI_GCP_*` environment variables. CLI host, port,
project, and the computed loopback base URL take precedence. Docker-backed services default to mock mode;
set the corresponding upstream `FLOCI_GCP_SERVICES_<SERVICE>_MOCK=false` to
opt into real containers. GCS and Pub/Sub run in process.

## Provenance and builds

`upstream.lock.json` pins floci-gcp 0.11.0 by commit and source SHA256. Upstream
publishes container images but no release binaries, so we build source with a
small Java CLI overlay, without maintaining a fork or mirroring upstream.
`FLOCI-LICENSE` preserves upstream's MIT notice. Our code is MIT licensed.

`python3 scripts/build.py` builds a JVM distribution for faster iteration;
run `java -jar dist/quarkus-app/quarkus-run.jar serve`. Add `--offline` to
require the cached source archive and Maven dependencies. Native compilation uses
a 6 GiB Java heap by default (`GCPSIM_BUILD_HEAP` overrides it); allow additional
RAM for compiler overhead. This is a build-time limit, not a runtime allocation. Runtime GCS/Pub/Sub
operations require no network outside loopback. Build dependencies require a
network on first use.

The CLI returns exit 0 for success, 2 for invalid input, 3 for startup failure.
Errors use a JSON `{"ok":false,"error":"..."}` envelope. Service responses
retain the real GCP wire format. Logs go to stderr.

## Test

See the [five-platform validation record](docs/validation.md) for live test evidence.

Install gcloud 543.0.0, then run:

```sh
python3 scripts/test-cli.py dist/gcpsim
python3 scripts/test-lifecycle.py dist/gcpsim
python3 scripts/test-gcloud.py dist/gcpsim
```

The integration test starts its own emulator on a free loopback port, isolates
all gcloud configuration, creates a bucket, uploads and downloads binary data,
creates a topic and subscription, publishes/pulls/acknowledges a message, deletes
resources, and stops the process even after failure. No existing cloud credentials
are used. Missing gcloud fails explicitly. See [decisions](docs/decisions.md) for
why gcloud is used instead of lmgcp today.

Push and pull-request CI build and test Linux x64/arm64, macOS x64/arm64, and
Windows x64. Tags `v*` publish binaries and SHA256SUMS only after all jobs pass. Native builds
are cached by source, version, build script, platform, and toolchain fingerprint;
every run tests the binary again, including cache hits.
Native Linux builds target Ubuntu 22.04 and require glibc and zlib; Windows
requires the Visual C++ runtime. macOS binaries are not notarized. Other upstream services and Docker-backed data planes are outside this
repository's current compatibility gate; no complete GCP fidelity is claimed.

## gcloud quickstart

With `gcpsim serve` running in another terminal:

```sh
export CLOUDSDK_CORE_PROJECT=gcpsim-local
export CLOUDSDK_AUTH_ACCESS_TOKEN=gcpsim-local-token
export CLOUDSDK_API_ENDPOINT_OVERRIDES_STORAGE=http://127.0.0.1:4588/storage/v1/
export CLOUDSDK_API_ENDPOINT_OVERRIDES_PUBSUB=http://127.0.0.1:4588/
gcloud storage buckets create gs://gcpsim-demo --location=us-central1
printf 'hello gcpsim\n' > hello.txt
gcloud storage cp hello.txt gs://gcpsim-demo/hello.txt
gcloud storage cat gs://gcpsim-demo/hello.txt
gcloud pubsub topics create gcpsim-demo
gcloud pubsub subscriptions create gcpsim-demo-sub --topic=gcpsim-demo
gcloud pubsub topics publish gcpsim-demo --message='hello gcpsim'
gcloud pubsub subscriptions pull gcpsim-demo-sub --auto-ack
```

These overrides are for the current shell. `*_EMULATOR_HOST` alone does not
redirect gcloud. Our gcloud tests exercise REST; the upstream gRPC services
remain available but are not independently certified by this test suite.
