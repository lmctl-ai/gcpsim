#!/usr/bin/env python3
"""Verify pinned upstream source, overlay the CLI, then build locally (no Docker)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--native', action='store_true')
p.add_argument('--offline', action='store_true', help='require cached source and Maven dependencies')
a = p.parse_args()
lock = json.loads((ROOT / 'upstream.lock.json').read_text(encoding='utf-8'))
archive = ROOT / '.cache/floci-gcp.tar.gz'
if not archive.exists():
    if a.offline:
        raise SystemExit('offline: source archive missing from .cache/floci-gcp.tar.gz')
    archive.parent.mkdir(exist_ok=True)
    url = f"https://codeload.github.com/floci-io/floci-gcp/tar.gz/{lock['commit']}"
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != lock['sha256']:
        raise SystemExit('upstream SHA256 mismatch')
    archive.write_bytes(payload)
if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['sha256']:
    raise SystemExit('upstream SHA256 mismatch')
source = ROOT / 'build' / ('floci-gcp-' + lock['commit'])
source.parent.mkdir(exist_ok=True)
with tarfile.open(archive) as tar:
    tar.extractall(source.parent, filter='data')
for path in (ROOT / 'src').rglob('*.java'):
    dest = source / path.relative_to(ROOT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(path.read_text(encoding='utf-8').replace('@VERSION@', (ROOT / 'VERSION').read_text(encoding='utf-8').strip())
                    .replace('@UPSTREAM@', lock['version']), encoding='utf-8')
cmd = [shutil.which('mvn') or 'mvn', '-B', 'package', '-Dmaven.test.skip=true',
       '-Dquarkus.package.main-class=ai.lmctl.gcpsim.Main']
if a.offline:
    cmd.append('-o')
if a.native:
    heap = os.environ.get('GCPSIM_BUILD_HEAP', '6g')
    cmd += ['-Pnative', '-Dquarkus.native.native-image-xmx=' + heap]
    if os.name == 'nt':
        # A Unix libc binding must not initialize while compiling on Windows.
        cmd.append('-Dquarkus.native.additional-build-args-append='
                   '--initialize-at-run-time=com.github.dockerjava.transport.DomainSocket')
subprocess.run(cmd, cwd=source, check=True)
dist = ROOT / 'dist'
dist.mkdir(exist_ok=True)
if a.native:
    suffix = '.exe' if os.name == 'nt' else ''
    shutil.copy2(source / 'target' / f"floci-gcp-{lock['version']}-runner{suffix}", dist / f'gcpsim{suffix}')
else:
    shutil.copytree(source / 'target/quarkus-app', dist / 'quarkus-app', dirs_exist_ok=True)
print('Built gcpsim in', dist)
