"""Tests for Shared-Everything GC atomic generation."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.sharedgc import _FAMILIES, sharedgc_gen
from miscast.modes import gen_sharedgc


def test_mode_shape():
    cases, untested = gen_sharedgc(None, 56)
    assert len(cases) == 56 and untested == []
    assert all(c[0].startswith("sharedgc") and c[2] == "f" for c in cases)


def test_all_shapes_assemble_and_validate():
    if not shutil.which("wasm-tools"):
        return
    families = set()
    for seed in range(56):
        label, export, expected, wat = sharedgc_gen(seed)
        families.add(seed % len(_FAMILIES))
        assert export == "f" and "miscast-sharedgc-stress" in wat
        assert expected == "TRAP" or expected.startswith("OK ")
        parsed = subprocess.run(
            ["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_sharedgc_test.wasm"],
            input=wat.encode(), capture_output=True,
        )
        assert parsed.returncode == 0, f"{label}: {parsed.stderr.decode()[:800]}"
        valid = subprocess.run(
            ["wasm-tools", "validate", "/tmp/_sharedgc_test.wasm", "--features=all"],
            capture_output=True,
        )
        assert valid.returncode == 0, f"{label}: {valid.stderr.decode()[:800]}"
    assert families == set(range(len(_FAMILIES)))
