"""Unit tests for the verdict classifiers — the tool's trust boundary.

Pure functions, no external tools, no third-party deps. Run: `python3 tests/test_verdict.py`
(or under pytest). Every reported class has a case here, including the i64 high-word
regression (a 32-bit mask once hid i64 value divergences)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.verdict import _ikey, _fkey, classify, classify_validation, classify_conformance


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


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
