"""Tests for remembered-set/write-barrier stress generation."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.barrier import MARKER, _FAMILIES, barrier_gen
from miscast.modes import gen_barrier


def test_mode_shape():
    cases, untested = gen_barrier(None, len(_FAMILIES))
    assert len(cases) == len(_FAMILIES)
    assert untested == []
    name, wat, export, args, expected, rtype = cases[0]
    assert name.startswith("barrier0|barrier-")
    assert MARKER in wat[:256]
    assert export == "f" and args == [] and expected.startswith("OK ") and rtype == "int"


def test_families_assemble_and_validate():
    if not shutil.which("wasm-tools"):
        return
    labels = set()
    for seed in range(len(_FAMILIES)):
        label, _export, _expected, wat = barrier_gen(seed)
        labels.add(label.removeprefix("barrier-"))
        parsed = subprocess.run(
            ["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_barrier_test.wasm"],
            input=wat.encode(), capture_output=True,
        )
        assert parsed.returncode == 0, f"{label}: {parsed.stderr.decode()[:800]}"
        valid = subprocess.run(
            ["wasm-tools", "validate", "/tmp/_barrier_test.wasm", "--features=all"],
            capture_output=True,
        )
        assert valid.returncode == 0, f"{label}: {valid.stderr.decode()[:800]}"
    assert labels == {"struct-set", "array-set", "array-fill", "array-copy", "table-copy", "nested-chain"}
