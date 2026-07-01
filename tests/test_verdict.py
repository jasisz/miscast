"""Unit tests for the verdict classifiers — the tool's trust boundary.

Pure functions, no external tools, no third-party deps. Run: `python3 tests/test_verdict.py`
(or under pytest). Every reported class has a case here, including the i64 high-word
regression (a 32-bit mask once hid i64 value divergences)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.verdict import _ikey, _fkey, classify, classify_validation, classify_conformance, classify_generated_script
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


def test_classify_selfcheck_expected_participates():
    # Generated self-checking modes carry a baked oracle. If the only live oracle is wrong but the SUT
    # matches the baked value, this must be an oracle split, not a false finding against the SUT.
    v = {"wasmedge": "OK 176816129", "wasmtime": "OK 7068"}
    eq("self-check expected prevents reciprocal false positive",
       classify(v, "wasmtime", "OK 7068", "int", expected_is_oracle=True), ("oracle-split", False))
    eq("self-check expected corroborates the good oracle and blames the bad SUT",
       classify(v, "wasmedge", "OK 7068", "int", expected_is_oracle=True), ("VALUE", True))


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


def test_classify_generated_script():
    eq("generated script self-oracle pass", classify_generated_script({"wasmtime": "PASS"}, "wasmtime"),
       ("agree", False))
    eq("generated script self-oracle fail", classify_generated_script({"wasmtime": "FAIL"}, "wasmtime"),
       ("VALUE", True))
    eq("generated script corroborated pass",
       classify_generated_script({"wasmtime": "PASS", "custom": "PASS"}, "custom"), ("agree", False))
    eq("generated script rejects unsupported",
       classify_generated_script({"wasmtime": "PASS", "custom": "UNSUP"}, "custom"), ("sut-reject", True))


def test_compose():
    # the consolidated compositional generator (chain / nest / exn shapes) that subsumes the old
    # castbr / externconvert / eh / exnstack modes. Pure structural checks; engine agreement is re-confirmed
    # by `python3 -m miscast.compose`.
    from miscast.compose import compose_gen, _CONDUITS, _heap, _KINDS, _BONUS
    eq("heap of a typed ref", _heap("(ref $arr)"), "$arr")
    eq("heap of i31", _heap("(ref i31)"), "i31")
    shapes, conduits, kinds = set(), set(), set()
    saw_isnull = saw_trap = False
    for s in range(60):
        label, export, expected, wat = compose_gen(s)
        eq(f"compose {s} exports f", export, "f")
        eq(f"compose {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"compose {s} expected is OK/TRAP", expected == "TRAP" or expected.startswith("OK "), True)
        parts = label.split("-")
        if parts[1] == "nest":                          # routed exception nest (subsumes exnstack)
            shapes.add("nest"); kinds.add(parts[2])
            if parts[2] in _KINDS:                       # GC payload: val < BONUS, so val // BONUS == catcher
                eq(f"compose {s} nest bonus encodes the catcher", int(expected.split()[1]) // _BONUS,
                   int(label.split("@L")[1]))
        elif parts[1] == "exn":                         # exnref carry / null trap (subsumes eh non-value)
            shapes.add("exn")
            saw_trap = saw_trap or expected == "TRAP"
        else:                                           # conduit chain (subsumes castbr / externconvert / eh-value)
            shapes.add("chain"); kinds.add(parts[1])
            conduits |= {c for c, _ in _CONDUITS if c in label}
            saw_isnull = saw_isnull or "isnull" in label
    eq("compose generates all three shapes", shapes, {"chain", "nest", "exn"})
    eq("compose sweeps every payload kind", set(_KINDS) <= kinds, True)
    eq("compose chain uses a variety of conduits", len(conduits) >= 6, True)
    eq("compose has a ref.is_null presence probe (castbr class)", saw_isnull, True)
    eq("compose has a mandated null-throw trap (eh class)", saw_trap, True)


def test_trapline():
    # the trap-boundary generator: each trappable op swept across its boundary with a baked TRAP or constant.
    # Pure structural checks; engine agreement re-confirmed by `python3 -m miscast.trapline`.
    from miscast.trapline import trapline_gen, _FAMILIES
    fams, saw_trap, saw_val = set(), False, False
    for s in range(40):
        label, export, expected, wat = trapline_gen(s)
        eq(f"trapline {s} exports f", export, "f")
        eq(f"trapline {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"trapline {s} expected is TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        fams.add(label.split("-")[1].split("[")[0])
        saw_trap = saw_trap or expected == "TRAP"
        saw_val = saw_val or expected.startswith("OK ")
    eq("trapline sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("trapline has mandated-TRAP probes", saw_trap, True)
    eq("trapline has baked-constant probes", saw_val, True)


def test_memory64():
    from miscast.memory64 import memory64_gen
    saw_trap = saw_ctrl = False
    for s in range(18):
        label, export, expected, wat = memory64_gen(s)
        eq(f"memory64 {s} exports f", export, "f")
        eq(f"memory64 {s} is a memory64 module", "(memory i64" in wat, True)
        eq(f"memory64 {s} expected TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        saw_trap = saw_trap or expected == "TRAP"
        saw_ctrl = saw_ctrl or "control-inbounds" in label
    eq("memory64 has high-address TRAP probes", saw_trap, True)
    eq("memory64 has an in-bounds control", saw_ctrl, True)


def test_memcross():
    # Multi-memory probes: cross-memory copy/init/fill/grow plus selected-memory OOB traps.
    from miscast.memcross import memcross_gen, _FAMILIES
    from miscast.modes import gen_memcross
    fams, saw_trap, saw_grow = set(), False, False
    for s in range(3 * len(_FAMILIES)):
        label, export, expected, wat = memcross_gen(s)
        eq(f"memcross {s} exports f", export, "f")
        eq(f"memcross {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"memcross {s} expected TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        fams.add(label.split("memcross-")[1])
        saw_trap = saw_trap or expected == "TRAP"
        saw_grow = saw_grow or "memory.grow $b" in wat
    cases, untested = gen_memcross(None, len(_FAMILIES))
    eq("memcross mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("memcross mode has no untested", untested, [])
    eq("memcross sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("memcross includes grow and trap probes", saw_grow and saw_trap, True)


def test_arrayops():
    from miscast.arrayops import arrayops_gen, _FAMILIES
    fams, saw_trap, saw_val = set(), False, False
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = arrayops_gen(s)
        eq(f"arrayops {s} exports f", export, "f")
        eq(f"arrayops {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"arrayops {s} expected TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        fams.add(label.split("-")[1].split("[")[0])
        saw_trap = saw_trap or expected == "TRAP"
        saw_val = saw_val or expected.startswith("OK ")
    eq("arrayops covers many families", len(fams) >= 7, True)
    labels = [arrayops_gen(s)[0] for s in range(2 * len(_FAMILIES))]
    eq("arrayops reproduces the widening copy (Wizard#656)", any("widening" in x for x in labels), True)
    eq("arrayops reproduces the dropped-segment surface (Wizard#657)", any("dropped" in x for x in labels), True)
    eq("arrayops has both TRAP and value probes", saw_trap and saw_val, True)


def test_callref():
    # typed function-reference / table-call probes: call_ref, call_indirect, table bulk ops,
    # branch-target casts, and mandated null / wrong-type traps.
    from miscast.callref import callref_gen, _FAMILIES
    from miscast.modes import gen_callref
    fams, saw_trap, saw_value = set(), False, False
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = callref_gen(s)
        eq(f"callref {s} exports f", export, "f")
        eq(f"callref {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"callref {s} expected TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        fams.add(label.split("callref-")[1])
        saw_trap = saw_trap or expected == "TRAP"
        saw_value = saw_value or expected.startswith("OK ")
    cases, untested = gen_callref(None, len(_FAMILIES))
    eq("callref mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("callref mode has no untested", untested, [])
    eq("callref sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("callref has both value and mandated-trap probes", saw_value and saw_trap, True)


def test_simdlane():
    # SIMD lane algebra: independent non-GC vector path for byte order, signedness, saturation and memory lanes.
    from miscast.simdlane import simdlane_gen, _FAMILIES
    from miscast.modes import gen_simdlane
    fams, saw_mem, saw_sat = set(), False, False
    for s in range(3 * len(_FAMILIES)):
        label, export, expected, wat = simdlane_gen(s)
        eq(f"simdlane {s} exports f", export, "f")
        eq(f"simdlane {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"simdlane {s} expected OK", expected.startswith("OK "), True)
        fams.add(label.split("simdlane-")[1])
        saw_mem = saw_mem or "load8_lane" in wat and "store8_lane" in wat
        saw_sat = saw_sat or "narrow_i16x8" in wat or "q15mulr_sat_s" in wat
    cases, untested = gen_simdlane(None, len(_FAMILIES))
    eq("simdlane mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("simdlane mode has no untested", untested, [])
    eq("simdlane sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("simdlane includes memory lanes and saturation probes", saw_mem and saw_sat, True)


def test_flowmerge():
    # Control-flow merge probes: refs through if/select/br/br_table/try_table plus stack-polymorphic dead code.
    from miscast.flowmerge import flowmerge_gen, _FAMILIES
    from miscast.modes import gen_flowmerge
    fams, saw_trap, saw_value = set(), False, False
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = flowmerge_gen(s)
        eq(f"flowmerge {s} exports f", export, "f")
        eq(f"flowmerge {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"flowmerge {s} expected TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        fams.add(label.split("flowmerge-")[1].split("[")[0])
        saw_trap = saw_trap or expected == "TRAP"
        saw_value = saw_value or expected.startswith("OK ")
    cases, untested = gen_flowmerge(None, len(_FAMILIES))
    eq("flowmerge mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("flowmerge mode has no untested", untested, [])
    eq("flowmerge sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("flowmerge has both value and mandated-trap probes", saw_value and saw_trap, True)


def test_refalias():
    # Reference identity probes: ref.eq observes alias preservation, not just field-value preservation.
    from miscast.refalias import refalias_gen, _FAMILIES
    from miscast.modes import gen_refalias
    fams, saw_alias, saw_not_alias = set(), False, False
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = refalias_gen(s)
        eq(f"refalias {s} exports f", export, "f")
        eq(f"refalias {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"refalias {s} expected OK", expected in ("OK 0", "OK 1"), True)
        fams.add(label.split("refalias-")[1])
        saw_alias = saw_alias or expected == "OK 1"
        saw_not_alias = saw_not_alias or expected == "OK 0"
    cases, untested = gen_refalias(None, len(_FAMILIES))
    eq("refalias mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("refalias mode has no untested", untested, [])
    eq("refalias sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("refalias has both positive and negative identity probes", saw_alias and saw_not_alias, True)


def test_mutalias():
    # Mutable-alias probes: mutate through one path and read through another.
    from miscast.mutalias import mutalias_gen, _FAMILIES
    from miscast.modes import gen_mutalias
    fams = set()
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = mutalias_gen(s)
        eq(f"mutalias {s} exports f", export, "f")
        eq(f"mutalias {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"mutalias {s} expected OK", expected.startswith("OK "), True)
        fams.add(label.split("mutalias-")[1])
    cases, untested = gen_mutalias(None, len(_FAMILIES))
    eq("mutalias mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("mutalias mode has no untested", untested, [])
    eq("mutalias sweeps every family", len(fams) >= len(_FAMILIES), True)


def test_heapstorm():
    # Stateful heap-storm probes: long aliasing operation sequences checked by a Python shadow checksum.
    from miscast.heapstorm import heapstorm_gen, _FAMILIES
    from miscast.modes import gen_heapstorm
    fams, saw_throw, saw_extern = set(), False, False
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = heapstorm_gen(s)
        eq(f"heapstorm {s} exports f", export, "f")
        eq(f"heapstorm {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"heapstorm {s} expected OK", expected.startswith("OK "), True)
        fams.add(label.split("heapstorm-")[1].rsplit("-", 1)[0])
        saw_throw = saw_throw or "throw_ref" in wat
        saw_extern = saw_extern or "extern.convert_any" in wat
    cases, untested = gen_heapstorm(None, len(_FAMILIES))
    eq("heapstorm mode emits one case per requested seed", len(cases), len(_FAMILIES))
    eq("heapstorm mode has no untested", untested, [])
    eq("heapstorm sweeps every scenario", len(fams) >= len(_FAMILIES), True)
    eq("heapstorm includes EH rethrow and extern scenarios", saw_throw and saw_extern, True)


def test_castalgebra():
    # the subtype/cast-correctness generator: ref.test / ref.cast swept across a fixed lattice, with
    # structural-twin canonicalization and self-consistency probes. Engine agreement re-confirmed by
    # `python3 -m miscast.castalgebra`.
    from miscast.castalgebra import castalgebra_gen, _FAMILIES
    fams, saw_trap, saw_val, saw_struct, saw_zero = set(), False, False, False, False
    for s in range(5 * len(_FAMILIES)):            # wide enough to reach every family's variants
        label, export, expected, wat = castalgebra_gen(s)
        eq(f"castalgebra {s} exports f", export, "f")
        eq(f"castalgebra {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"castalgebra {s} expected TRAP/OK", expected == "TRAP" or expected.startswith("OK "), True)
        fams.add(label.split("-")[1].split("[")[0])
        saw_trap = saw_trap or expected == "TRAP"
        saw_val = saw_val or expected not in ("TRAP", "OK 0")
        saw_struct = saw_struct or "structural" in label
        saw_zero = saw_zero or expected == "OK 0"
    eq("castalgebra sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("castalgebra reproduces structural-twin canonicalization (talos/wasmz)", saw_struct, True)
    eq("castalgebra has a mandated cast TRAP", saw_trap, True)
    eq("castalgebra has positive (membership/field) and zero (non-membership/consistent) answers",
       saw_val and saw_zero, True)
    eq("castalgebra has the deeper rec-group / func-subtyping / nullability families",
       {"recgroup", "funcsub", "nullable"} <= fams, True)


def test_constinit():
    # the GC const-expr init generator: global / elem / data initialised with struct.new / array.new /
    # ref.i31 (+ extended-const), read to a baked value. Engine agreement re-confirmed by
    # `python3 -m miscast.constinit`.
    from miscast.constinit import constinit_gen, _FAMILIES
    fams, saw_i31, saw_ext, saw_elem = set(), False, False, False
    for s in range(5 * len(_FAMILIES)):            # wide enough to reach every family's variants
        label, export, expected, wat = constinit_gen(s)
        eq(f"constinit {s} exports f", export, "f")
        eq(f"constinit {s} is a module", wat.strip().startswith("(module"), True)
        eq(f"constinit {s} expected is a value", expected.startswith("OK "), True)
        fams.add(label.split("constinit-")[1].split("[")[0])    # family names contain hyphens (global-struct)
        saw_i31 = saw_i31 or "global-i31" in label       # wasmz#4 crash surface
        saw_ext = saw_ext or "extconst" in label          # extended-const in a GC const-expr
        saw_elem = saw_elem or "elem" in label
    eq("constinit sweeps every family", len(fams) >= len(_FAMILIES), True)
    eq("constinit covers the ref.i31 global (wasmz#4 surface)", saw_i31, True)
    eq("constinit covers extended-const arithmetic in a const-expr", saw_ext, True)
    eq("constinit covers elem-segment const-exprs", saw_elem, True)
    eq("constinit has the deeper i31-edge / packed / segments families",
       {"i31edge", "packed", "segments"} <= fams, True)


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
