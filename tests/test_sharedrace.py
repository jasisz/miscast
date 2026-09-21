"""Tests for deterministic multi-worker Shared-Everything cases."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.sharedrace import _FAMILIES, sharedrace_gen
from miscast.modes import gen_sharedrace


def test_mode_shape():
    cases, untested = gen_sharedrace(None, 80)
    assert len(cases) == 80 and untested == []
    assert all(c[2] == "__shared_workers__" and c[4] == "OK 0" for c in cases)


def test_all_shapes_assemble_and_validate():
    if not shutil.which("wasm-tools"):
        return
    families = set()
    for seed in range(80):
        label, export, expected, wat = sharedrace_gen(seed)
        families.add(seed % len(_FAMILIES))
        assert export == "__shared_workers__" and expected == "OK 0"
        parsed = subprocess.run(
            ["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_sharedrace_test.wasm"],
            input=wat.encode(), capture_output=True,
        )
        assert parsed.returncode == 0, f"{label}: {parsed.stderr.decode()[:800]}"
        valid = subprocess.run(
            ["wasm-tools", "validate", "/tmp/_sharedrace_test.wasm", "--features=all"],
            capture_output=True,
        )
        assert valid.returncode == 0, f"{label}: {valid.stderr.decode()[:800]}"
    assert families == set(range(len(_FAMILIES)))
