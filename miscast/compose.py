"""Compositional feature-interaction generator: mix orthogonal mechanisms, generate programs from the mix.

Every soundness scalp miscast has found lives at a FEATURE INTERACTION (exceptions × GC, array.copy × GC,
br_on_cast × value-forwarding). The siloed modes (`eh` / `castbr` / `externconvert` / `exnstack`) are each one
fixed interaction. This generator factors them into four orthogonal mechanisms and recombines them, so it
emits programs no single mode does — and stays self-checking because of one unifying law:

    a value carried through a CONDUIT that the spec says is value-preserving must come out intact.

So the oracle is computable BY CONSTRUCTION through any composition — the expected result is just the
payload's value, no matter how many conduits it threaded or what stressed it.

  * PAYLOAD   — a GC thing with a known read value: a mutable array element, a struct field, an i31, or an
                array nested in a struct.
  * CONDUIT   — a value-preserving wrapper the reference passes through: an exception tag (throw→catch), a
                function call, a mutable global (store→load), or a br_on_cast branch. Chained 1–3 deep, MIXED.
  * STRESSOR  — a forced garbage collection (heap churn) between producing the reference and reading it; it
                must not change the result.
  * CONSUMER  — read the reference DIRECTLY off the stack, or first store it to a LOCAL.

A divergence is the SUT mangling the reference somewhere in the mix; the label names the exact recipe.
"""
import random

from .exnstack import _payload                          # the 4 payload kinds (kind, ptype, build, read, val)


def _heap(ptype):
    return ptype.replace("(ref ", "").rstrip(")")        # "(ref $arr)" -> "$arr" ; "(ref i31)" -> "i31"


def _default(ptype):
    """A fresh, default-valued reference of the same type (a second operand for the `select` conduit)."""
    h = _heap(ptype)
    return {"$arr": "(array.new_default $arr 4)",
            "$st": "(struct.new_default $st)",
            "$box": "(struct.new $box (array.new_default $arr 4))",
            "i31": "(ref.i31 (i32.const 0))"}[h]


class _Ctx:
    """Collects the module-level pieces the conduits need, with a fresh-id counter."""
    def __init__(self):
        self.types, self.tables, self.tags, self.globals, self.funcs, self.n = [], [], [], [], [], 0

    def fresh(self):
        self.n += 1
        return self.n


def c_tag(build, ptype, ctx):
    """Throw the reference through an exception tag and catch it (the wasmz #9 conduit)."""
    i = ctx.fresh()
    ctx.tags.append(f"  (tag $e{i} (param {ptype}))")
    return f"(block $c{i} (result {ptype}) (try_table (catch $e{i} $c{i}) (throw $e{i} {build})) (unreachable))"


def c_call(build, ptype, ctx):
    """Pass the reference through a function-call boundary (identity)."""
    i = ctx.fresh()
    ctx.funcs.append(f"  (func $pass{i} (param $x {ptype}) (result {ptype}) (local.get $x))")
    return f"(call $pass{i} {build})"


def c_global(build, ptype, ctx):
    """Store the reference into a mutable global and load it back."""
    i = ctx.fresh()
    ctx.globals.append(f"  (global $g{i} (mut (ref null {_heap(ptype)})) (ref.null {_heap(ptype)}))")
    return (f"(block (result {ptype}) (global.set $g{i} {build}) "
            f"(ref.as_non_null (global.get $g{i})))")


def c_broncast(build, ptype, ctx):
    """Forward the reference through a br_on_cast branch (it must reach the label as the cast target)."""
    i = ctx.fresh()
    return f"(block $b{i} (result {ptype}) (br_on_cast $b{i} (ref null any) {ptype} {build}) (unreachable))"


def c_tailcall(build, ptype, ctx):
    """Pass the reference through a TAIL call — `$tc` tail-calls identity, which returns it to `$tc`'s caller,
    so the reference survives a frame REPLACEMENT (the tail-call × GC-ref seam)."""
    i = ctx.fresh()
    ctx.funcs.append(f"  (func $id{i} (param $x {ptype}) (result {ptype}) (local.get $x))")
    ctx.funcs.append(f"  (func $tc{i} (param $x {ptype}) (result {ptype}) (return_call $id{i} (local.get $x)))")
    return f"(call $tc{i} {build})"


def c_field(build, ptype, ctx):
    """Store the reference into a fresh GC struct field and read it straight back (a heap field, vs a global)."""
    i = ctx.fresh()
    ctx.types.append(f"  (type $h{i} (struct (field {ptype})))")
    return f"(struct.get $h{i} 0 (struct.new $h{i} {build}))"


def c_table(build, ptype, ctx):
    """Store the reference into a table slot and read it back."""
    i = ctx.fresh()
    ctx.tables.append(f"  (table $t{i} 1 (ref null {_heap(ptype)}))")
    return (f"(block (result {ptype}) (table.set $t{i} (i32.const 0) {build}) "
            f"(ref.as_non_null (table.get $t{i} (i32.const 0))))")


def c_extern(build, ptype, ctx):
    """Round-trip the reference out to `externref` and back (`extern.convert_any` / `any.convert_extern`)."""
    return f"(ref.cast {ptype} (any.convert_extern (extern.convert_any {build})))"


_CONDUITS = [("tag", c_tag), ("call", c_call), ("global", c_global), ("broncast", c_broncast),
             ("tailcall", c_tailcall), ("field", c_field), ("table", c_table), ("extern", c_extern)]


def compose_gen(seed):
    """Return (label, export, expected, wat): a payload threaded through a random MIX of conduits, optionally
    stressed by a forced GC, read directly or via a local. Expected = the payload value (conduits preserve it)."""
    rng = random.Random(seed)
    kind, ptype, build, read, val = _payload(seed)
    ctx = _Ctx()
    chain = [rng.choice(_CONDUITS) for _ in range(1 + seed % 3)]     # 1..3 conduits, mixed (repeats allowed)
    for _name, fn in chain:
        build = fn(build, ptype, ctx)
    stressor = (seed % 3 == 0)
    consumer = "local" if (stressor or (seed // 3) % 2 == 1) else "direct"   # gc forces a rooted local

    if consumer == "direct":
        consume = f"    {read}"
    else:
        churn = ("\n    (block $cd (loop $cl "
                 "(br_if $cd (i32.ge_u (local.get $ci) (i32.const 2000))) "
                 "(drop (array.new_default $arr (i32.const 1000))) "
                 "(local.set $ci (i32.add (local.get $ci) (i32.const 1))) (br $cl)))") if stressor else ""
        consume = f"    (local.set $p){churn}\n    (local.get $p) {read}"

    pieces = "".join(s + "\n" for s in ctx.types + ctx.tables + ctx.tags + ctx.globals + ctx.funcs)
    wat = (f"(module\n"
           f"  (type $arr (array (mut i32)))\n"
           f"  (type $st (struct (field i32) (field i32)))\n"
           f"  (type $box (struct (field (ref $arr))))\n"
           f"{pieces}"
           f"  (func (export \"f\") (result i32) (local $p {ptype}) (local $ci i32)\n"
           f"    {build}\n{consume}))")
    recipe = "+".join(name for name, _ in chain)
    label = f"compose-{kind}-{recipe}{'-gc' if stressor else ''}-{consumer[:3]}"
    return label, "f", f"OK {val}", wat


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
        return f"OK {m[-1]}" if m else "?:" + (out.strip().splitlines()[-1][:34] if out.strip() else "")

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y,exceptions=y,tail-call=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== compose: conformant engines must agree (payload survives any conduit mix + stressor) ===")
    bad = 0
    for s in range(28):
        label, export, expected, wat = compose_gen(s)
        open("/tmp/cp.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/cp.wat", "-o", "/tmp/cp.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/cp.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:34} exp={expected:8} {row}")
    print(f"\n28 programs, {bad} disagreeing with the oracle")
