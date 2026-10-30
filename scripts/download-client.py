#!/usr/bin/env python3
"""Download the pinned lmgcp release asset, verify its committed SHA-256, then install."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import subprocess
import urllib.request

root = Path(__file__).resolve().parents[1]
lock = json.loads((root / 'client.lock.json').read_text(encoding='utf-8'))
system = {'Linux': 'linux', 'Darwin': 'macos', 'Windows': 'windows'}[platform.system()]
arch = {'x86_64': 'x64', 'AMD64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}[platform.machine()]
asset = f'lmgcp-{system}-{arch}' + ('.exe' if system == 'windows' else '')
url = f'https://github.com/lmctl-ai/lmgcp/releases/download/{lock["tag"]}/{asset}'
with urllib.request.urlopen(url, timeout=90) as response:
    data = response.read()
if hashlib.sha256(data).hexdigest() != lock['sha256'][asset]:
    sys.exit('client checksum mismatch')
path = root / 'dist' / ('lmgcp.exe' if system == 'windows' else 'lmgcp')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(data)
path.chmod(0o755)
version = subprocess.check_output([str(path), '--version'], text=True, encoding='utf-8', timeout=15).strip()
if version != 'lmgcp ' + lock['tag'].removeprefix('v'):
    sys.exit('client version mismatch')
print(f'Verified {asset} from {lock["tag"]}: {path}')
