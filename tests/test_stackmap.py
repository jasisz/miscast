"""Tests for the GC stack-map/compiler-liveness stress oracle."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.modes import gen_stackmap
from miscast.stackmap import MARKER, _FAMILIES, stackmap_gen


def _assemble_and_run(wat, export, execute=False):
    parsed = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_stackmap_test.wasm"],
                            input=wat.encode(), capture_output=True)
    assert parsed.returncode == 0, parsed.stderr.decode()[:500]
    valid = subprocess.run(["wasm-tools", "validate", "/tmp/_stackmap_test.wasm", "--features=all"],
                           capture_output=True)
    assert valid.returncode == 0, valid.stderr.decode()[:500]
    if not execute or not shutil.which("wasmtime"):
        return None
    run = subprocess.run([
        "wasmtime", "run", "--invoke", export, "-W", "function-references=y,gc=y",
        "-C", "collector=copying", "-O", "gc-zeal-alloc-counter=1", "/tmp/_stackmap_test.wasm",
    ], capture_output=True, text=True)
    assert run.returncode == 0, (run.stdout + run.stderr)[:500]
    return "OK " + run.stdout.strip().splitlines()[-1]


def test_mode_shape():
    cases, untested = gen_stackmap(None, len(_FAMILIES))
    assert len(cases) == len(_FAMILIES)
    assert untested == []
    name, wat, export, args, expected, rtype = cases[0]
    assert name.startswith("stackmap0|stackmap-")
    assert MARKER in wat[:256]
    assert export == "f" and args == [] and expected.startswith("OK ") and rtype == "int"


def test_families_assemble_and_self_check():
    if not shutil.which("wasm-tools"):
        return
    labels = set()
    for seed in range(len(_FAMILIES)):
        label, export, expected, wat = stackmap_gen(seed)
        labels.add(label.split("-", 2)[1])
        # Assemble every IR shape; execute the two endpoint shapes. Running
        # every shape sequentially with collection-at-every-allocation makes CI needlessly slow; the CLI
        # integration sweep covers the full matrix in parallel.
        got = _assemble_and_run(wat, export, execute=seed in (0, len(_FAMILIES) - 1))
        if got is not None:
            assert got == expected, f"{label}: got {got}, expected {expected}"
    assert labels == {"locals", "operand", "multivalue", "loop", "mixed"}
