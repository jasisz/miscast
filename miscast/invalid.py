"""A curated battery of spec-INVALID GC modules for the validation differential.

Each module is rejected by the ground-truth validator (`wasm-tools validate`) and by every conformant
engine, but exports a runnable `f` so that an invoke-only SUT is still decisive: an engine that *accepts and
runs* it (returns a value) has no validator for that rule and is unsound. The battery covers the two classes
the hunt found maturing engines missing — **type-section subtyping** (a subtype that narrows / drops /
retypes a field, extends a `final` type, or exceeds the depth limit) and **operand-stack typing** (a block /
function result of the wrong type or arity, a non-defaultable `array.new_default`).
"""


def _deep_chain(n):
    """t0..tn, each a subtype of the previous — a hierarchy of depth n. The spec caps subtype depth at 63,
    so n=64 must be rejected."""
    types = ["(type $t0 (sub (struct (field i32))))"]
    types += [f"(type $t{i} (sub $t{i - 1} (struct (field i32))))" for i in range(1, n + 1)]
    return "(module\n  " + "\n  ".join(types) + '\n  (func (export "f") (result i32) (i32.const 1)))'


# (label, wat, reason) — every one is invalid; f returns a value only if the SUT skipped the check.
MODULES = [
    ("subtype-field-retype",
     '(module (rec (type $base (sub (struct (field (mut i32))))) (type $sub (sub $base (struct (field (mut i64))))))'
     ' (func (export "f") (result i32) (i32.const 1)))',
     "subtype retypes a supertype field (i32 -> i64)"),
    ("subtype-drops-field",
     '(module (rec (type $base (sub (struct (field i32) (field i32)))) (type $sub (sub $base (struct (field i32)))))'
     ' (func (export "f") (result i32) (i32.const 1)))',
     "subtype drops a supertype field"),
    ("subtype-immutable-retype",
     '(module (rec (type $base (sub (struct (field i32)))) (type $sub (sub $base (struct (field f32)))))'
     ' (func (export "f") (result i32) (i32.const 1)))',
     "subtype retypes an immutable field (i32 -> f32)"),
    ("extends-final",
     '(module (rec (type $base (sub final (struct (field i32)))) (type $sub (sub $base (struct (field i32)))))'
     ' (func (export "f") (result i32) (i32.const 1)))',
     "subtype extends a final type"),
    ("subtype-depth-64", _deep_chain(64),
     "subtype hierarchy depth 64 exceeds the spec maximum of 63"),
    ("block-result-type",
     '(module (func (export "f") (result i32) (block (result i32) (i64.const 7))))',
     "block leaves i64 where i32 is declared"),
    ("func-return-type",
     '(module (func (export "f") (result i32) (i64.const 99)))',
     "function body produces i64 where i32 is declared"),
    ("func-result-arity",
     '(module (func (export "f") (result i32) (i32.const 1) (i32.const 2)))',
     "function leaves 2 values where 1 is declared"),
    ("array-nondefaultable",
     '(module (type $s (struct (field i32))) (type $arr (array (mut (ref $s))))'
     ' (func (export "f") (result i32) (drop (array.new_default $arr (i32.const 3))) (i32.const 1)))',
     "array.new_default on a non-defaultable element type"),
]


def segs():
    """The battery as validation segments (the shape the validation differential consumes)."""
    return [{"name": f"invalid:{label}", "module": wat, "kind": "invalid",
             "reason": reason, "stateful": False, "actions": []}
            for label, wat, reason in MODULES]


if __name__ == "__main__":
    import subprocess, os
    os.environ["DYLD_LIBRARY_PATH"] = "/tmp/we017/lib"
    WT = "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime"

    def rejects(tool, wat):
        open("/tmp/iv.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/iv.wat", "-o", "/tmp/iv.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            return "ASMFAIL"            # didn't even assemble — not a clean "invalid module" probe
        if tool == "wasm-tools":
            r = subprocess.run(["wasm-tools", "validate", "/tmp/iv.wasm"], capture_output=True, text=True)
        else:
            r = subprocess.run([WT, "compile", "-W", "function-references=y,gc=y", "/tmp/iv.wasm", "-o", "/tmp/iv.cwasm"], capture_output=True, text=True)
        return "REJECT" if r.returncode != 0 else "ACCEPT"

    print("=== invalid battery: ground truth must REJECT every module ===")
    bad = 0
    for label, wat, reason in MODULES:
        wt_v = rejects("wasm-tools", wat); wt_c = rejects("wasmtime", wat)
        ok = wt_v == "REJECT" and wt_c == "REJECT"
        bad += not ok
        print(f"  {'OK ' if ok else 'XX '}{label:24} wasm-tools={wt_v} wasmtime={wt_c}  — {reason}")
    print(f"\n{len(MODULES) - bad}/{len(MODULES)} are cleanly invalid (assemble + rejected by both validators)")
