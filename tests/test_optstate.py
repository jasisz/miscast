"""Tests for hot GC optimizer-state generation."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.modes import gen_optstate
from miscast.optstate import MARKER, _FAMILIES, optstate_gen


def test_mode_shape():
    cases, untested = gen_optstate(None, len(_FAMILIES))
    assert len(cases) == len(_FAMILIES) and untested == []
    assert all(case[0].startswith("optstate") and MARKER in case[1][:256] for case in cases)


def test_families_assemble_and_validate():
    if not shutil.which("wasm-tools"):
        return
    labels = set()
    for seed in range(len(_FAMILIES)):
        label, _export, expected, wat = optstate_gen(seed)
        labels.add(label)
        assert expected.startswith("OK ")
        parsed = subprocess.run(
            ["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_optstate_test.wasm"],
            input=wat.encode(), capture_output=True,
        )
        assert parsed.returncode == 0, f"{label}: {parsed.stderr.decode()[:1000]}"
        valid = subprocess.run(
            ["wasm-tools", "validate", "/tmp/_optstate_test.wasm", "--features=all"],
            capture_output=True,
        )
        assert valid.returncode == 0, f"{label}: {valid.stderr.decode()[:1000]}"
    assert len(labels) == len(_FAMILIES)
