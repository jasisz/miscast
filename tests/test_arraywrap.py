"""Tests for GC-array extent-wrap generation."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.arraywrap import _FAMILIES, arraywrap_gen
from miscast.modes import gen_arraywrap


def test_mode_shape():
    cases, untested = gen_arraywrap(None, 42)
    assert len(cases) == 42 and untested == []
    assert all(case[0].startswith("arraywrap") and case[2] == "f" for case in cases)


def test_all_shapes_assemble_and_validate():
    if not shutil.which("wasm-tools"):
        return
    labels = set()
    for seed in range(42):
        label, _export, expected, wat = arraywrap_gen(seed)
        labels.add(label.split("[", 1)[0])
        assert expected == "TRAP" or expected.startswith("OK ")
        parsed = subprocess.run(
            ["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_arraywrap_test.wasm"],
            input=wat.encode(), capture_output=True,
        )
        assert parsed.returncode == 0, f"{label}: {parsed.stderr.decode()[:800]}"
        valid = subprocess.run(
            ["wasm-tools", "validate", "/tmp/_arraywrap_test.wasm", "--features=all"],
            capture_output=True,
        )
        assert valid.returncode == 0, f"{label}: {valid.stderr.decode()[:800]}"
    assert labels == {
        "arraywrap-fill", "arraywrap-copy", "arraywrap-init-data-i8", "arraywrap-init-data-i32",
        "arraywrap-new-data-i8", "arraywrap-new-data-i32", "arraywrap-init-elem", "arraywrap-new-elem",
    }
