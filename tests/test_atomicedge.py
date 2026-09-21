"""Tests for deterministic shared-memory atomic probes."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.atomicedge import _FAMILIES, atomicedge_gen
from miscast.modes import gen_atomicedge


def test_mode_shape():
    cases, untested = gen_atomicedge(None, 56)
    assert len(cases) == 56 and untested == []
    assert all(c[0].startswith("atomicedge") and c[2] == "f" and c[5] == "int" for c in cases)


def test_all_shapes_assemble_and_validate():
    if not shutil.which("wasm-tools"):
        return
    families = set()
    for seed in range(56):
        label, export, expected, wat = atomicedge_gen(seed)
        families.add(seed % len(_FAMILIES))
        assert export == "f"
        assert expected == "TRAP" or expected.startswith("OK ")
        assert "shared" in wat and ".atomic." in wat
        parsed = subprocess.run(
            ["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_atomicedge_test.wasm"],
            input=wat.encode(), capture_output=True,
        )
        assert parsed.returncode == 0, f"{label}: {parsed.stderr.decode()[:800]}"
        valid = subprocess.run(
            ["wasm-tools", "validate", "/tmp/_atomicedge_test.wasm", "--features=all"],
            capture_output=True,
        )
        assert valid.returncode == 0, f"{label}: {valid.stderr.decode()[:800]}"
    assert families == set(range(len(_FAMILIES)))
