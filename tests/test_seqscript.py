"""Unit tests for generated stateful .wast scripts."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.seqscript import seqscript_gen, write_seqscript_groups, _FAMILIES, WASMTIME_WAST_FLAGS


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_generates_stateful_scripts():
    labels = set()
    for seed in range(2 * len(_FAMILIES)):
        label, actions, script = seqscript_gen(seed)
        eq("label prefix", label.startswith("seqscript-"), True)
        eq("has several stateful actions", actions >= 3, True)
        eq("is a wast script", script.lstrip().startswith("(module"), True)
        eq("has script assertions",
           "(assert_return" in script or "(assert_trap" in script or "(assert_unlinkable" in script, True)
        labels.add(label)
    eq("covers every family", len(labels) >= len(_FAMILIES), True)


def test_writes_and_wasmtime_accepts():
    groups = write_seqscript_groups(len(_FAMILIES))
    eq("one group per family", len(groups), len(_FAMILIES))
    for g in groups:
        eq("path exists", os.path.exists(g["src"]), True)
        eq("group is generated", g["generated"], True)
    if not shutil.which("wasmtime"):
        print("  (skip: wasmtime not on PATH)")
        return
    for g in groups:
        p = subprocess.run(["wasmtime", "wast", "-W", WASMTIME_WAST_FLAGS, g["src"]],
                           capture_output=True, text=True)
        assert p.returncode == 0, f"{g['name']} failed: {(p.stderr or p.stdout)[:300]}"


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
