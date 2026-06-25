"""br_on_cast / br_on_cast_fail value-forwarding differential.

When `br_on_cast` takes its branch (the cast succeeds) the spec forwards the OPERAND — now typed as the cast
target — to the branch label; `br_on_cast_fail` does the same on its fall-through. An engine that keeps the
control flow correct but forwards a NULL (or a wrong reference) instead corrupts the value silently. Each
program therefore reads a field of the cast result and returns it, self-checking against the value the
conformant engines agree on: an engine that forwarded null traps on the field read (or returns the wrong
value). No second engine required.
"""

# each shape returns (wat, expected) — shapes 0/1 read the forwarded operand by VALUE (a null-forward shows
# as a wrong value, not a trap, so the mode flags it by default); shape 2 reads a field (a null-forward
# traps); shape 3 exercises br_on_cast_fail's fall-through path.
_SHAPES = [
    # 0: ref.is_null on the forwarded operand — expected 0 (it is non-null); a null-forward returns 1.
    lambda v: ('''(module
  (type $s (struct (field i32)))
  (func (export "f") (result i32)
    (block $hit (result (ref $s))
      (br_on_cast $hit anyref (ref $s) (struct.new $s (i32.const %d)))
      (unreachable))
    (ref.is_null)))''' % v, 0),
    # 1: ref.test (ref $s) on the forwarded operand — expected 1; a null-forward returns 0.
    lambda v: ('''(module
  (type $s (struct (field i32)))
  (func (export "f") (result i32)
    (block $hit (result (ref $s))
      (br_on_cast $hit anyref (ref $s) (struct.new $s (i32.const %d)))
      (unreachable))
    (ref.test (ref $s))))''' % v, 1),
    # 2: struct.get the forwarded operand's field — expected v; a null-forward traps (null dereference).
    lambda v: ('''(module
  (type $s (struct (field i32)))
  (func (export "f") (result i32)
    (block $hit (result (ref $s))
      (br_on_cast $hit anyref (ref $s) (struct.new $s (i32.const %d)))
      (unreachable))
    (struct.get $s 0)))''' % v, v),
    # 3: br_on_cast_fail falls through (cast succeeded); the fall-through (ref $s) must carry the field.
    lambda v: ('''(module
  (type $s (struct (field i32)))
  (func (export "f") (result i32)
    (block $fail (result anyref)
      (br_on_cast_fail $fail anyref (ref $s) (struct.new $s (i32.const %d)))
      (return (struct.get $s 0)))
    (drop)
    (i32.const -1)))''' % v, v),
]


def gen(seed, val=None):
    v = 40 + (seed * 11) % 900 if val is None else val
    return _SHAPES[seed % len(_SHAPES)](v)


if __name__ == "__main__":
    import subprocess, re, os
    os.environ["DYLD_LIBRARY_PATH"] = "/tmp/we017/lib"
    WT = "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime"; WE = "/tmp/we017/bin/wasmedge"
    TALOS = "/tmp/talos-probe/interpreter/.lake/build/bin/runner"; WZ = "/tmp/wzrel/wasmz"
    NODE = "/Users/szymon.tezewski/.nvm/versions/node/v26.3.0/bin/node"
    V8 = "/Users/szymon.tezewski/PycharmProjects/miscast/miscast/oracle/v8.js"

    def verdict(p):
        low = ((p.stdout or "") + (p.stderr or "")).lower()
        if any(k in low for k in ("trap", "unreachable", "null reference", "error", "exception")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"RUN {m[-1]}" if m else "?:" + (low.strip().splitlines()[-1][:30] if low.strip() else "")

    def run(eng, wat):
        open("/tmp/cb.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/cb.wat", "-o", "/tmp/cb.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            return "ASMFAIL:" + a.stderr.strip().splitlines()[-1][:44]
        if eng == "wt": p = subprocess.run([WT, "run", "-W", "function-references=y,gc=y", "--invoke", "f", "/tmp/cb.wasm"], capture_output=True, text=True)
        elif eng == "we": p = subprocess.run([WE, "run", "/tmp/cb.wasm", "f"], capture_output=True, text=True)
        elif eng == "talos": p = subprocess.run([TALOS, "/tmp/cb.wat", "f"], capture_output=True, text=True)
        elif eng == "wz": p = subprocess.run([WZ, "/tmp/cb.wasm", "f"], capture_output=True, text=True)
        elif eng == "v8": p = subprocess.run([NODE, V8, "/tmp/cb.wasm", "f"], capture_output=True, text=True)
        return verdict(p)

    print("=== br_on_cast value-forwarding: conformant must RUN <val>, wasmz forwards null ===")
    for s in range(len(_SHAPES)):
        wat, val = gen(s)
        row = {e: run(e, wat) for e in ["wt", "we", "v8", "talos", "wz"]}
        conf_ok = row["wt"] == row["we"] == row["v8"] == f"RUN {val}"
        print(f"  shape {s} (val={val}): {row}  gate={'OK' if conf_ok else 'FAIL'}")
