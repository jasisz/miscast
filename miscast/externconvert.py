"""extern.convert_any / any.convert_extern round-trip differential.

The GC spec requires `any.convert_extern (extern.convert_any x) = x` for non-null `x`, and null maps to
null — pushing a GC reference out to `externref` and back must preserve it (and its identity). Each program
is self-checking: it round-trips a freshly built struct through `externref` and reads its field back, so the
conformant verdict is the original value. An engine that traps or returns a different value — e.g. one that
has not implemented the conversion opcode pair and lowers it to a trap — diverges, with no second engine
required.
"""

_SHAPES = [
    # 0: round-trip a struct out to externref and back, read the field.
    lambda v: f'''(module
  (type $s (struct (field (mut i32))))
  (func (export "rt") (result i32)
    (local $e externref)
    (local.set $e (extern.convert_any (struct.new $s (i32.const {v}))))
    (struct.get $s 0 (ref.cast (ref $s) (any.convert_extern (local.get $e))))))''',
    # 1: round-trip, but go through ref.is_null on the way back too (null must survive as non-null here).
    lambda v: f'''(module
  (type $s (struct (field (mut i32))))
  (func (export "rt") (result i32)
    (local $o (ref $s)) (local $e externref)
    (local.set $o (struct.new $s (i32.const {v})))
    (local.set $e (extern.convert_any (local.get $o)))
    (if (result i32) (ref.is_null (any.convert_extern (local.get $e)))
      (then (i32.const -1))
      (else (struct.get $s 0 (ref.cast (ref $s) (any.convert_extern (local.get $e))))))))''',
]


def gen(seed, val=None):
    val = 100 + (seed * 13) % 9000 if val is None else val
    return _SHAPES[seed % len(_SHAPES)](val), val


if __name__ == "__main__":
    import subprocess, re, os
    os.environ["DYLD_LIBRARY_PATH"] = "/tmp/we017/lib"
    WT = "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime"; WE = "/tmp/we017/bin/wasmedge"
    TALOS = "/tmp/talos-probe/interpreter/.lake/build/bin/runner"; WZ = "/tmp/wzrel/wasmz"
    NODE = "/Users/szymon.tezewski/.nvm/versions/node/v26.3.0/bin/node"
    V8 = "/Users/szymon.tezewski/PycharmProjects/miscast/miscast/oracle/v8.js"

    def verdict(p):
        out = (p.stdout or "") + (p.stderr or "")
        m = re.findall(r"-?\d+", p.stdout or "")
        low = out.lower()
        if "trap" in low or "unreachable" in low:
            return "TRAP"
        if m:
            return f"RUN {m[-1]}"
        return "?:" + (out.strip().splitlines()[-1][:30] if out.strip() else "")

    def run(eng, wat):
        open("/tmp/ec.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/ec.wat", "-o", "/tmp/ec.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            return "ASMFAIL:" + a.stderr.strip().splitlines()[-1][:40]
        if eng == "wt": p = subprocess.run([WT, "run", "-W", "function-references=y,gc=y", "--invoke", "rt", "/tmp/ec.wasm"], capture_output=True, text=True)
        elif eng == "we": p = subprocess.run([WE, "run", "/tmp/ec.wasm", "rt"], capture_output=True, text=True)
        elif eng == "talos": p = subprocess.run([TALOS, "/tmp/ec.wat", "rt"], capture_output=True, text=True)
        elif eng == "wz": p = subprocess.run([WZ, "/tmp/ec.wasm", "rt"], capture_output=True, text=True)
        elif eng == "v8": p = subprocess.run([NODE, V8, "/tmp/ec.wasm", "rt"], capture_output=True, text=True)
        return verdict(p)

    print("=== extern-convert: conformant must RUN <val>, divergence = trap/other ===")
    for s in range(len(_SHAPES)):
        wat, val = gen(s)
        row = {e: run(e, wat) for e in ["wt", "we", "v8", "talos", "wz"]}
        conf_ok = row["wt"] == row["we"] == row["v8"] == f"RUN {val}"
        print(f"  shape {s} (val={val}): {row}  gate={'OK' if conf_ok else 'FAIL'}")
