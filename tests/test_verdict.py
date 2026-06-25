"""Unit tests for the verdict classifiers — the tool's trust boundary.

Pure functions, no external tools, no third-party deps. Run: `python3 tests/test_verdict.py`
(or under pytest). Every reported class has a case here, including the i64 high-word
regression (a 32-bit mask once hid i64 value divergences)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.verdict import _ikey, _fkey, classify, classify_validation, classify_conformance
from miscast.engines import _CRASH
from miscast.wast import _norm_arg


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_ikey_width():
    eq("i32 sign-normalization", _ikey("OK 4294967295", 32), _ikey("OK -1", 32))
    eq("i64 high-word stays visible", _ikey("OK 4294967297", 64) != _ikey("OK 1", 64), True)
    eq("ref is not an int key", _ikey("OK ref"), None)
    eq("void is not an int key", _ikey("OK _"), None)


def test_fkey():
    eq("any NaN canonicalizes", _fkey("OK -nan"), "nan")
    eq("equal floats match", _fkey("OK 1.5") == _fkey("OK 1.5"), True)
    eq("different floats differ", _fkey("OK 1.5") != _fkey("OK 2.5"), True)


def test_classify_soundness():
    v = {"v8": "TRAP", "wasmtime": "TRAP", "spec": "TRAP", "custom": "OK _"}
    eq("runs where all oracles trap", classify(v, "custom", None, "int"), ("SOUNDNESS", True))


def test_classify_value_i32():
    v = {"v8": "OK 1", "spec": "OK 1", "custom": "OK 0"}
    eq("i32 value diff", classify(v, "custom", None, "int"), ("VALUE", True))


def test_classify_value_i64_highword_regression():
    # B1: a divergence only in the high 32 bits must NOT be masked away.
    v = {"v8": "OK 1", "spec": "OK 1", "custom": "OK 4294967297"}
    eq("i64 high-word -> VALUE", classify(v, "custom", None, "int64"), ("VALUE", True))
    eq("32-bit mask would have hidden it", classify(v, "custom", None, "int"), ("agree", False))


def test_classify_completeness_and_reject():
    eq("SUT over-traps", classify({"v8": "OK 1", "spec": "OK 1", "custom": "TRAP"}, "custom", None, "int"),
       ("completeness", True))
    eq("SUT over-rejects", classify({"v8": "OK 1", "spec": "OK 1", "custom": "UNSUP"}, "custom", None, "int"),
       ("sut-reject", True))


def test_classify_split_and_agree():
    eq("oracles split on status", classify({"v8": "OK 1", "spec": "TRAP", "custom": "OK 1"}, "custom", None, "int"),
       ("oracle-split", False))
    eq("oracles split on value", classify({"v8": "OK 1", "spec": "OK 2", "custom": "OK 1"}, "custom", None, "int"),
       ("oracle-split", False))
    eq("all agree", classify({"v8": "OK 1", "spec": "OK 1", "custom": "OK 1"}, "custom", None, "int"),
       ("agree", False))


def test_classify_crash():
    # the SUT's engine fell over (a HOST crash, not a Wasm trap) on a module the oracles ran -> CRASH finding
    eq("host crash is a CRASH finding",
       classify({"v8": "OK 1", "spec": "OK 1", "custom": "CRASH"}, "custom", None, "int"), ("CRASH", True))
    # a CRASH in an oracle is excluded from the runnable pool — never a confounder
    eq("a crashed oracle does not poison consensus",
       classify({"v8": "OK 1", "spec": "CRASH", "custom": "OK 1"}, "custom", None, "int"), ("agree", False))


def test_classify_sut_na():
    # the SUT couldn't receive the action's arguments (CUSTOM_NO_ARGS) -> skipped, never a finding
    eq("an arg-incapable SUT is skipped",
       classify({"v8": "OK 1", "spec": "OK 1", "custom": "SUT_NA"}, "custom", None, "int"), ("sut-na", False))


def test_crash_regex():
    eq("a JVM uncaught exception is a crash",
       bool(_CRASH.search('Exception in thread "main" java.lang.ArrayIndexOutOfBoundsException: 1')), True)
    eq("a native segfault is a crash", bool(_CRASH.search("Segmentation fault: 11")), True)
    eq("a Rust panic is a crash", bool(_CRASH.search("thread 'main' panicked at src/lib.rs")), True)
    eq("a clean Wasm trap is NOT a crash", bool(_CRASH.search("wasm trap: out of bounds memory access")), False)


def test_classify_validation():
    eq("accepts an invalid module", classify_validation(
        {"wtools": "REJECT", "spec": "REJECT", "custom": "ACCEPT"}, "custom"), ("SOUNDNESS", True))
    eq("rejects like the oracles", classify_validation(
        {"wtools": "REJECT", "spec": "REJECT", "custom": "REJECT"}, "custom"), ("agree", False))
    eq("an oracle disagrees", classify_validation(
        {"wtools": "REJECT", "spec": "ACCEPT", "custom": "REJECT"}, "custom"), ("oracle-split", False))
    eq("SUT not probeable", classify_validation(
        {"wtools": "REJECT", "spec": "REJECT", "custom": "UNSUP"}, "custom"), ("sut-unsup", False))


def test_classify_conformance():
    eq("SUT fails where oracle passes", classify_conformance({"spec": "PASS", "custom": "FAIL"}, "custom"),
       ("SOUNDNESS", True))
    eq("conforms", classify_conformance({"spec": "PASS", "custom": "PASS"}, "custom"), ("agree", False))
    eq("one-shot SUT can't run a script", classify_conformance({"spec": "PASS", "custom": "n/a"}, "custom"),
       ("sut-stateful-na", False))


def test_eh_battery():
    # the exception-handling self-check battery is structurally well-formed (no toolchain needed here;
    # engine agreement is re-confirmed by `python3 -m miscast.eh`).
    from miscast.eh import gen, count, _PROGRAMS
    eq("eh battery is non-empty", count() > 0, True)
    eq("eh count matches programs", count(), len(_PROGRAMS))
    for label, export, expected, wat in _PROGRAMS:
        eq(f"eh {label} has an export", isinstance(export, str) and bool(export), True)
        eq(f"eh {label} expected is OK/TRAP", expected == "TRAP" or expected.startswith("OK "), True)
        eq(f"eh {label} is a module", wat.strip().startswith("(module"), True)
        eq(f"eh {label} exercises EH", ("try_table" in wat or "throw_ref" in wat), True)
    eq("eh gen cycles by seed", gen(0), gen(count()))


def test_norm_arg():
    # spec operands are written in hex; a SUT's CLI may parse hex as 0, so normalize to signed decimal.
    eq("i32 hex positive", _norm_arg("i32", "0x7fffffff"), "2147483647")
    eq("i32 hex sign bit", _norm_arg("i32", "0x80000000"), "-2147483648")
    eq("i32 all-ones is -1", _norm_arg("i32", "0xffffffff"), "-1")
    eq("i64 hex sign bit", _norm_arg("i64", "0x8000000000000000"), "-9223372036854775808")
    eq("decimal passes through", _norm_arg("i32", "-1"), "-1")
    eq("plain decimal", _norm_arg("i64", "42"), "42")


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
