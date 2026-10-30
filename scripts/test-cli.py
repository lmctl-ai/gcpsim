#!/usr/bin/env python3
"""Behavioral CLI checks, shared by JVM and native builds."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
exe = Path(sys.argv[1]).resolve()
command = [os.environ.get('JAVA', 'java'), '-Dfile.encoding=UTF-8', '-Dstdout.encoding=UTF-8', '-Dstderr.encoding=UTF-8', '-jar', str(exe)] if exe.suffix == '.jar' else [str(exe)]

def run(args, code=0):
    r = subprocess.run(command + args, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert r.returncode == code, (args, r.returncode, r.stdout, r.stderr)
    return r.stdout

assert run(['--version']).strip() == f"gcpsim {(ROOT / 'VERSION').read_text(encoding='utf-8').strip()} (floci-gcp 0.11.0)"
help_text = run(['--help'])
assert '4588' in help_text and 'gcpsim — local GCP emulator' in help_text
assert 'Usage:' in run([])
for args in [['serve', '--auth', 'bogus'], ['serve', '--auth', 'enforce'], ['bogus'], ['serve', '--port'], ['serve', '--port', '0'],
             ['serve', '--port', '65536'], ['serve', '--port', 'abc'],
             ['serve', '--host', 'example.com'], ['serve', '--unknown', 'x'],
             ['serve', '--project', 'invalid/project'], ['serve', '--port', '4588', '--port', '4589']]:
    assert json.loads(run(args, 2))['ok'] is False
print('PASS: 14 CLI checks')
