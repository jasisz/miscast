"""Rec-group canonicalization differential.

Wasm GC uses ISO-recursive typing, so a recursion group's canonical form is sensitive to MEMBER ORDER:
canonicalization substitutes a group's internal type references with POSITIONAL (de-Bruijn) recursive
indices, so reordering the members changes those indices and yields DISTINCT canonical types — even when
the unrolled infinite trees coincide (iso-recursive, not equi-recursive). Two rec groups holding the same
mutually-recursive types in a different order therefore define DIFFERENT types, and a `call_indirect`
against one type on a function of the other MUST trap.

Each program is a trap-differential: the conformant verdict is `TRAP`. A SUT that returns a value is
unsound — it executed an indirect call through a type mismatch because it canonicalizes equi-recursively
(by unrolling / bisimulation) instead of by positional rec-group structure. (This is the opposite direction
from a too-strict subtype check: here the engine is too LOOSE and runs ill-typed code.)
"""

# shapes whose conformant verdict is TRAP (validated: wasmtime/WasmEdge/V8 all trap, Talos runs)
_SHAPES = [
    # 0: A = func(ref B) -> i32, B = struct(ref A); G1 = [A;B], reordered G2 = [B;A].
    lambda ret: f'''(module
  (rec (type $A1 (func (param (ref null $B1)) (result i32))) (type $B1 (struct (field (ref null $A1)))))
  (rec (type $B2 (struct (field (ref null $A2)))) (type $A2 (func (param (ref null $B2)) (result i32))))
  (func $f (type $A1) i32.const {ret})
  (table 1 funcref) (elem (i32.const 0) $f)
  (func (export "go") (result i32) (call_indirect (type $A2) (ref.null $B2) (i32.const 0))))''',
    # 1: recursive reference in the RESULT instead of the parameter.
    lambda ret: f'''(module
  (rec (type $A1 (func (result (ref null $B1)))) (type $B1 (struct (field (ref null $A1)))))
  (rec (type $B2 (struct (field (ref null $A2)))) (type $A2 (func (result (ref null $B2)))))
  (func $f (type $A1) (ref.null $B1))
  (table 1 funcref) (elem (i32.const 0) $f)
  (func (export "go") (result i32) (drop (call_indirect (type $A2) (i32.const 0))) (i32.const {ret})))''',
    # 2: three-way cycle A -> B -> C -> A, group rotated by one ([A;B;C] vs [B;C;A]).
    lambda ret: f'''(module
  (rec (type $A1 (func (param (ref null $B1)) (result i32))) (type $B1 (struct (field (ref null $C1)))) (type $C1 (struct (field (ref null $A1)))))
  (rec (type $B2 (struct (field (ref null $C2)))) (type $C2 (struct (field (ref null $A2)))) (type $A2 (func (param (ref null $B2)) (result i32))))
  (func $f (type $A1) i32.const {ret})
  (table 1 funcref) (elem (i32.const 0) $f)
  (func (export "go") (result i32) (call_indirect (type $A2) (ref.null $B2) (i32.const 0))))''',
]


def gen(seed, ret=None):
    ret = 40 + (seed * 7) % 900 if ret is None else ret
    return _SHAPES[seed % len(_SHAPES)](ret)


if __name__ == "__main__":
    import subprocess, re, os
    os.environ["DYLD_LIBRARY_PATH"] = "/tmp/we017/lib"
    WT = "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime"; WE = "/tmp/we017/bin/wasmedge"
    TALOS = "/tmp/talos-probe/interpreter/.lake/build/bin/runner"; WZ = "/tmp/wzrel/wasmz"
    NODE = "/Users/szymon.tezewski/.nvm/versions/node/v26.3.0/bin/node"
    V8 = "/Users/szymon.tezewski/PycharmProjects/miscast/miscast/oracle/v8.js"

    def verdict(p):
        low = ((p.stdout or "") + (p.stderr or "")).lower()
        if any(k in low for k in ("trap", "unreachable", "mismatch", "error", "exception")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"RUN {m[-1]}" if m else "?:" + (low.strip().splitlines()[-1][:30] if low.strip() else "")

    def run(eng, wat):
        open("/tmp/rg.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/rg.wat", "-o", "/tmp/rg.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            return "ASMFAIL:" + a.stderr.strip().splitlines()[-1][:40]
        if eng == "wt": p = subprocess.run([WT, "run", "-W", "function-references=y,gc=y", "--invoke", "go", "/tmp/rg.wasm"], capture_output=True, text=True)
        elif eng == "we": p = subprocess.run([WE, "run", "/tmp/rg.wasm", "go"], capture_output=True, text=True)
        elif eng == "talos": p = subprocess.run([TALOS, "/tmp/rg.wat", "go"], capture_output=True, text=True)
        elif eng == "wz": p = subprocess.run([WZ, "/tmp/rg.wasm", "go"], capture_output=True, text=True)
        elif eng == "v8": p = subprocess.run([NODE, V8, "/tmp/rg.wasm", "go"], capture_output=True, text=True)
        return verdict(p)

    print("=== rec-group: conformant must TRAP, Talos must RUN (unsound) ===")
    for s in range(len(_SHAPES)):
        row = {e: run(e, gen(s)) for e in ["wt", "we", "v8", "talos", "wz"]}
        conf_trap = row["wt"] == row["we"] == row["v8"] == "TRAP"
        talos_runs = row["talos"].startswith("RUN")
        print(f"  shape {s}: {row}  gate={'OK' if conf_trap else 'FAIL'}  talos_diverges={talos_runs}")
