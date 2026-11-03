#!/usr/bin/env python3
"""The committed public version floor plus run number yields stable versions."""
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
current = (root / "VERSION").read_text(encoding="utf-8").strip()
match = re.fullmatch(r"(0\.2)\.(\d+)", current)
assert match, current
patch = int(match[2])

def derive(*args):
    return subprocess.check_output(["bash", root / "scripts/derive-release-version.sh", *args], text=True).strip()

assert derive("--diagnostic") == current
assert derive("1") == f"0.2.{patch + 1}"
assert derive("19") == f"0.2.{patch + 19}"
assert derive("19") == f"0.2.{patch + 19}"
workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
assert "branches: [main]" in workflow and "tags: ['v*']" not in workflow
assert "needs: [version, native]" in workflow and "needs.version.outputs.release" in workflow
assert "scripts/publish-release.sh" in workflow
print("PASS gcpsim run-derived version and main release gate")
