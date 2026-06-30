"""The spec-INVALID GC validation battery — loaded from `corpus/invalid/*.wat`.

Most files are modules rejected by the ground-truth validator (`wasm-tools validate`) and by every conformant
engine, but they export a runnable `f` so an invoke-only SUT is still decisive: an engine that *accepts and
runs* one has no validator for that rule and is unsound. Each file carries a `;; reason:` header. The battery
covers **type-section subtyping** (a subtype that narrows / drops / retypes a field, extends a `final` type,
or violates function-subtyping variance), **operand-stack typing** (a block / function result of the wrong
type or arity, a non-defaultable `array.new_default`, a mismatched `call_ref` callee), and
**reference-type casts** (a `ref.test` / `ref.cast` whose target heap type is in a different hierarchy than
the operand; a `br_on_cast` / `br_on_cast_fail` whose target label cannot receive the forwarded operand).
The depth-limit case is retained as a soft implementation-limit probe and should not be reported upstream
as a standalone spec violation. Add a case by dropping a new `.wat` into `corpus/invalid/`.
"""
import glob
import os

CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corpus", "invalid")


def _reason(text):
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(";; reason:"):
            return s[len(";; reason:"):].strip()
    return ""


def _load():
    """(label, wat, reason) per `corpus/invalid/*.wat` — label from the filename (a leading `NN_` ordering
    prefix is dropped), reason from the `;; reason:` header. The `wat` keeps the comment; `wasm-tools`
    ignores it, and it travels into the reproducer."""
    out = []
    for path in sorted(glob.glob(os.path.join(CORPUS, "*.wat"))):
        text = open(path).read()
        label = os.path.splitext(os.path.basename(path))[0]
        if len(label) > 3 and label[:2].isdigit() and label[2] == "_":
            label = label[3:]
        out.append((label, text, _reason(text)))
    return out


MODULES = _load()


def segs():
    """The battery as validation segments (the shape the validation differential consumes)."""
    return [{"name": f"invalid:{label}", "module": wat, "kind": "invalid",
             "reason": reason, "stateful": False, "actions": []}
            for label, wat, reason in MODULES]


if __name__ == "__main__":
    import shutil
    import subprocess
    WT = os.environ.get("WASMTIME_BIN") or shutil.which("wasmtime")

    def rejects(tool, wat):
        open("/tmp/iv.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/iv.wat", "-o", "/tmp/iv.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            return "ASMFAIL"
        if tool == "wasm-tools":
            r = subprocess.run(["wasm-tools", "validate", "/tmp/iv.wasm"], capture_output=True, text=True)
        else:
            if not WT:
                return "UNSUP"
            r = subprocess.run([WT, "compile", "-W", "function-references=y,gc=y", "/tmp/iv.wasm", "-o", "/tmp/iv.cwasm"], capture_output=True, text=True)
        return "REJECT" if r.returncode != 0 else "ACCEPT"

    print(f"=== invalid battery ({len(MODULES)} files from {CORPUS}): ground truth must REJECT every module ===")
    bad = 0
    for label, wat, reason in MODULES:
        wt_v = rejects("wasm-tools", wat); wt_c = rejects("wasmtime", wat)
        ok = wt_v == "REJECT" and wt_c == "REJECT"
        bad += not ok
        print(f"  {'OK ' if ok else 'XX '}{label:28} wasm-tools={wt_v} wasmtime={wt_c}  — {reason}")
    print(f"\n{len(MODULES) - bad}/{len(MODULES)} are cleanly invalid (assemble + rejected by both validators)")
