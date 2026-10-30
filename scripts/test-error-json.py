#!/usr/bin/env python3
"""Compile the owned error renderer with adversarial values against the built Jackson jars."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
java = Path(os.environ.get('JAVA_HOME', '')) / 'bin' / ('java.exe' if os.name == 'nt' else 'java')
javac = java.with_name('javac.exe' if os.name == 'nt' else 'javac')
classpath = os.pathsep.join(str(p) for p in (ROOT / 'dist/quarkus-app/lib/main').glob('*jackson*.jar'))
assert classpath, 'Build the JVM distribution first'
with tempfile.TemporaryDirectory(prefix='gcpsim-error-json-') as temp:
    subprocess.run([str(javac), '-encoding', 'UTF-8', '-cp', classpath, '-d', temp,
                    str(ROOT / 'src/main/java/ai/lmctl/gcpsim/ErrorJson.java'),
                    str(ROOT / 'tests/java/ErrorJsonTest.java')], check=True)
    subprocess.run([str(java), '-cp', temp + os.pathsep + classpath, 'ai.lmctl.gcpsim.ErrorJsonTest'], check=True)
