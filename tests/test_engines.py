"""Unit tests for engine backends that can be exercised without external runtimes."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast import engines


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


class FakeRun:
    def __init__(self, result):
        self.result = result
        self.cmds = []

    def __call__(self, cmd, timeout=None):
        self.cmds.append(cmd)
        return self.result


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["fake"], rc, out, err)


def with_fake_run(result, fn):
    old = engines._run
    fake = FakeRun(result)
    engines._run = fake
    try:
        return fn(fake)
    finally:
        engines._run = old


def test_wasmedge_run_ok_value():
    def body(fake):
        got = engines.be_wasmedge("m.wat", "m.wasm", "f", [("i32", "7")])
        eq("result", got, "OK 42")
        eq("uses reactor invoke", fake.cmds[0][-3:], ["m.wasm", "f", "7"])
    with_fake_run(cp(out="42\n"), body)


def test_wasmedge_run_classifies_trap_noexport_and_unsup():
    with_fake_run(cp(1, err="execution failed: out of bounds memory access"), lambda _f: eq(
        "trap", engines.be_wasmedge("m.wat", "m.wasm", "f", []), "TRAP"))
    with_fake_run(cp(1, err='Function "missing" not found in the module export list.'), lambda _f: eq(
        "no export", engines.be_wasmedge("m.wat", "m.wasm", "missing", []), "NOEXPORT"))
    with_fake_run(cp(1, err="validation failed: sub type"), lambda _f: eq(
        "validation/load failure", engines.be_wasmedge("m.wat", "m.wasm", "f", []), "UNSUP"))


def test_wasmedge_validate_accept_reject():
    with_fake_run(cp(0), lambda _f: eq("accepted module", engines._wasmedge_validate("m.wasm"), "ACCEPT"))
    with_fake_run(cp(1, err="validation failed: sub type"), lambda _f: eq(
        "rejected module", engines._wasmedge_validate("m.wasm"), "REJECT"))


def test_wamr_run_ok_trap_and_crash():
    with_fake_run(cp(out="0xffffffff:i32\n"), lambda fake: (
        eq("hex result", engines.be_wamr("m.wat", "m.wasm", "f", []), "OK 0xffffffff"),
        eq("heap extension disabled", "--heap-size=0" in fake.cmds[0], True)))
    with_fake_run(cp(1, err="Exception: out of bounds array access"), lambda _f: eq(
        "trap", engines.be_wamr("m.wat", "m.wasm", "f", []), "TRAP"))
    with_fake_run(cp(1, err="ERROR: AddressSanitizer: heap-buffer-overflow"), lambda _f: eq(
        "sanitizer", engines.be_wamr("m.wat", "m.wasm", "f", []), "CRASH"))


def test_wamr_validate_accept_reject():
    with_fake_run(cp(1, err="Exception: lookup function __miscast_validate_only__ failed"), lambda _f: eq(
        "loaded module", engines._wamr_validate("m.wasm"), "ACCEPT"))
    with_fake_run(cp(255, err="WASM module load failed: type mismatch"), lambda _f: eq(
        "rejected module", engines._wamr_validate("m.wasm"), "REJECT"))


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
