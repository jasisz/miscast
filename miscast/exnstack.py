"""Generative exception-handling unwind oracle: the generator IS the oracle.

A nest of `try_table` handlers with selective tag matching catches an innermost `throw` that carries a GC
payload; because exception routing is statically decidable — the thrown tag and each handler's catch set are
fixed at generation time — the Python side simulates the unwind exactly and bakes the expected result, with
NO second engine. This reaches an unwind depth and routing complexity the hand-written `eh` fixtures can't,
and sweeps two extra axes so it explores beyond the one known scalp:

  * PAYLOAD KIND carried through the tag — a mutable array (read at a non-zero index), a two-field struct, an
    `i31`, or a nested struct-holding-an-array — so a reference of ANY kind corrupted across the unwind shows
    as a wrong value, not just the array case (the wasmz #9 class is array+direct; the others are new ground);
  * CONSUME pattern — the forwarded reference read DIRECTLY off the catch-forwarded block result, or first
    stored to a LOCAL — since the corruption that hits the direct path may spare the local path (and vice
    versa on a different engine).

Each handler adds `level*100000`, so a throw MIS-ROUTED to the wrong handler surfaces as a wrong value too.
The nearest *enclosing* handler whose catch set holds the thrown tag fires (innermost→outermost), and the
outermost level catches every tag so a catcher always exists. Expected = `payload_value + catcher*100000`.
"""
import random

_TAGS = ("a", "b", "c")
_BONUS = 100000
_KINDS = ("array", "struct", "i31", "nested")


def _payload(seed):
    """A GC payload carried through the tag: its heap type, the build expression, the read sequence (in STACK
    form — the reference is already on the operand stack), and the value a correct read yields."""
    base = 7000 + (seed * 13) % 2000
    kind = _KINDS[seed % len(_KINDS)]
    if kind == "array":
        idx = 1 + seed % 3
        elems = [base + j * 10 for j in range(4)]
        fill = " ".join(f"(i32.const {e})" for e in elems)
        return kind, "(ref $arr)", f"(array.new_fixed $arr 4 {fill})", \
            f"(i32.const {idx}) (array.get $arr)", elems[idx]
    if kind == "struct":
        f = seed % 2
        fs = [base, base + 5]
        return kind, "(ref $st)", f"(struct.new $st (i32.const {fs[0]}) (i32.const {fs[1]}))", \
            f"(struct.get $st {f})", fs[f]
    if kind == "i31":
        return kind, "(ref i31)", f"(ref.i31 (i32.const {base}))", "(i31.get_s)", base
    # nested: a struct whose field is an array; read an element through the struct
    idx = 1 + seed % 3
    elems = [base + j * 10 for j in range(4)]
    fill = " ".join(f"(i32.const {e})" for e in elems)
    return kind, "(ref $box)", f"(struct.new $box (array.new_fixed $arr 4 {fill}))", \
        f"(struct.get $box 0) (i32.const {idx}) (array.get $arr)", elems[idx]


def exnstack_gen(seed):
    """Return (label, export, expected, wat) for the seed-th generated unwind program."""
    rng = random.Random(seed)
    K = 3 + seed % 6                                   # nest depth 3..8
    kind, ptype, build, read, pval = _payload(seed)
    consume = "direct" if (seed // 4) % 2 == 0 else "local"   # independent of kind (=seed%4) -> all 8 combos
    subsets = [set(_TAGS)]                             # level 0 (outermost) catches all -> a catcher always exists
    for _ in range(1, K):
        subsets.append(set(rng.sample(_TAGS, rng.randint(1, len(_TAGS)))))
    throw_tag = rng.choice(_TAGS)
    catcher = max(i for i in range(K) if throw_tag in subsets[i])   # innermost→outermost: largest matching index
    expected = pval + catcher * _BONUS

    def handler(i):
        bonus = f"(i32.const {i * _BONUS}) (i32.add) (return)"
        if consume == "direct":                       # read the forwarded reference straight off the stack
            return f"    {read}\n    {bonus}"
        return f"    (local.set $p) (local.get $p) {read}\n    {bonus}"   # store to a local first, then read

    def emit(i):
        catches = " ".join(f"(catch ${t} $L{i})" for t in sorted(subsets[i]))
        inner = f"        (throw ${throw_tag} {build})" if i == K - 1 else emit(i + 1)
        return (f"  (block $L{i} (result {ptype})\n"
                f"    (try_table {catches}\n{inner})\n"
                f"    (unreachable))\n{handler(i)}")

    tags = "\n  ".join(f"(tag ${t} (param {ptype}))" for t in _TAGS)
    local = f" (local $p {ptype})" if consume == "local" else ""
    wat = (f"(module\n"
           f"  (type $arr (array (mut i32)))\n"
           f"  (type $st (struct (field i32) (field i32)))\n"
           f"  (type $box (struct (field (ref $arr))))\n"
           f"  {tags}\n"
           f"  (func (export \"f\") (result i32){local}\n"
           f"{emit(0)}))")
    return f"exnstack-d{K}-{kind}-{consume[:3]}-{throw_tag}@L{catcher}", "f", f"OK {expected}", wat


if __name__ == "__main__":
    import subprocess, os
    os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("WASMEDGE_LIB", os.path.expanduser("~/wasm-engines/wasmedge/lib"))
    ENG = os.path.expanduser("~/wasm-engines")
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WT = f"{ENG}/wasmtime-v46/wasmtime"; WE = f"{ENG}/wasmedge/bin/wasmedge"
    MCR = f"{REPO}/runner/target/release/mc-runner"
    NODE = os.path.expanduser("~/.nvm/versions/node/v26.3.0/bin/node"); V8 = f"{REPO}/miscast/oracle/v8.js"

    def res(p):
        out = ((p.stdout or "") + (p.stderr or "")).lower()
        if "trap" in out or "unreachable" in out or "exception" in out or "null" in out:
            return "TRAP"
        import re
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "?:" + (out.strip().splitlines()[-1][:30] if out.strip() else "")

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y,exceptions=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== exnstack: conformant engines must agree with the baked simulator oracle (all kinds × consume) ===")
    bad = 0
    for s in range(24):
        label, export, expected, wat = exnstack_gen(s)
        open("/tmp/es.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/es.wat", "-o", "/tmp/es.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:60]}"); bad += 1; continue
        row = {e: run(e, "/tmp/es.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:34} exp={expected:8} {row}")
    print(f"\n24 programs, {bad} disagreeing with the simulator")
