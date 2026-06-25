"""Compositional feature-interaction generator: mix orthogonal mechanisms, generate programs from the mix.

Every soundness scalp miscast has found lives at a FEATURE INTERACTION (exceptions × GC, array.copy × GC,
br_on_cast × value-forwarding). This generator factors that whole family — the `castbr` / `externconvert` /
`eh` / `exnstack` modes are each one fixed corner of it — into orthogonal mechanisms and recombines them,
staying self-checking by one unifying law:

    a value carried through a CONDUIT that the spec says is value-preserving must come out intact.

So the oracle is computable BY CONSTRUCTION through any composition. Three program SHAPES cover the family:

  * CHAIN   — a PAYLOAD (array element / struct field / i31 / nested) threaded through a random MIX of 1–3
              CONDUITS (exception tag / call / global / br_on_cast / tail-call / struct field / table / extern
              round-trip), optionally STRESSED by a forced GC, read DIRECT or via a LOCAL, for its VALUE (a
              corrupted payload diverges) or for `ref.is_null` (a null / wrong-type forward diverges — the
              `castbr` class). Subsumes `castbr`, `externconvert`, and the value cases of `eh`.
  * NEST    — the payload thrown into a nest of `try_table` handlers with selective tag matching; the nearest
              enclosing handler fires and each adds `level*100000`, so a MIS-ROUTED throw surfaces as a wrong
              value. Subsumes `exnstack`.
  * EXN     — an `exnref` captured via `catch_ref` / `catch_all_ref`, carried through a GC field / global,
              re-raised via `throw_ref` and recovered; plus the mandated null-`throw_ref` TRAP. Subsumes the
              non-value cases of `eh`.

A divergence is the SUT mangling the reference (or mis-routing the throw) somewhere in the mix; the label is
the recipe.
"""
import random

_KINDS = ("array", "struct", "i31", "nested")


def _payload(seed):
    """A GC payload carried through a conduit: its heap type, the build expression, the read sequence (STACK
    form — the reference is already on the stack), and the value a correct read yields."""
    base = 7000 + (seed * 13) % 2000
    kind = _KINDS[seed % len(_KINDS)]
    if kind == "array":
        idx = 1 + seed % 3
        elems = [base + j * 10 for j in range(4)]
        fill = " ".join(f"(i32.const {e})" for e in elems)
        return kind, "(ref $arr)", f"(array.new_fixed $arr 4 {fill})", f"(i32.const {idx}) (array.get $arr)", elems[idx]
    if kind == "struct":
        f = seed % 2
        fs = [base, base + 5]
        return kind, "(ref $st)", f"(struct.new $st (i32.const {fs[0]}) (i32.const {fs[1]}))", f"(struct.get $st {f})", fs[f]
    if kind == "i31":
        return kind, "(ref i31)", f"(ref.i31 (i32.const {base}))", "(i31.get_s)", base
    idx = 1 + seed % 3
    elems = [base + j * 10 for j in range(4)]
    fill = " ".join(f"(i32.const {e})" for e in elems)
    return kind, "(ref $box)", f"(struct.new $box (array.new_fixed $arr 4 {fill}))", \
        f"(struct.get $box 0) (i32.const {idx}) (array.get $arr)", elems[idx]


def _heap(ptype):
    return ptype.replace("(ref ", "").rstrip(")")        # "(ref $arr)" -> "$arr" ; "(ref i31)" -> "i31"


def _default(ptype):
    h = _heap(ptype)
    return {"$arr": "(array.new_default $arr 4)", "$st": "(struct.new_default $st)",
            "$box": "(struct.new $box (array.new_default $arr 4))", "i31": "(ref.i31 (i32.const 0))"}[h]


_FIXED = ("  (type $arr (array (mut i32)))\n"
          "  (type $st (struct (field i32) (field i32)))\n"
          "  (type $box (struct (field (ref $arr))))\n")


class _Ctx:
    """Collects the module-level pieces the conduits need, with a fresh-id counter."""
    def __init__(self):
        self.types, self.tables, self.tags, self.globals, self.funcs, self.n = [], [], [], [], [], 0

    def fresh(self):
        self.n += 1
        return self.n

    def module(self, ptype, body, locals=""):
        pieces = "".join(s + "\n" for s in self.types + self.tables + self.tags + self.globals + self.funcs)
        return (f"(module\n{_FIXED}{pieces}"
                f"  (func (export \"f\") (result i32){locals}\n{body}))")


# ---- conduits: each wraps a ref-producing expression, passing it through a value-preserving construct ----
def c_tag(build, ptype, ctx):
    i = ctx.fresh()
    ctx.tags.append(f"  (tag $e{i} (param {ptype}))")
    return f"(block $c{i} (result {ptype}) (try_table (catch $e{i} $c{i}) (throw $e{i} {build})) (unreachable))"


def c_call(build, ptype, ctx):
    i = ctx.fresh()
    ctx.funcs.append(f"  (func $pass{i} (param $x {ptype}) (result {ptype}) (local.get $x))")
    return f"(call $pass{i} {build})"


def c_global(build, ptype, ctx):
    i = ctx.fresh()
    ctx.globals.append(f"  (global $g{i} (mut (ref null {_heap(ptype)})) (ref.null {_heap(ptype)}))")
    return f"(block (result {ptype}) (global.set $g{i} {build}) (ref.as_non_null (global.get $g{i})))"


def c_broncast(build, ptype, ctx):
    i = ctx.fresh()
    return f"(block $b{i} (result {ptype}) (br_on_cast $b{i} (ref null any) {ptype} {build}) (unreachable))"


def c_tailcall(build, ptype, ctx):
    i = ctx.fresh()
    ctx.funcs.append(f"  (func $id{i} (param $x {ptype}) (result {ptype}) (local.get $x))")
    ctx.funcs.append(f"  (func $tc{i} (param $x {ptype}) (result {ptype}) (return_call $id{i} (local.get $x)))")
    return f"(call $tc{i} {build})"


def c_field(build, ptype, ctx):
    i = ctx.fresh()
    ctx.types.append(f"  (type $h{i} (struct (field {ptype})))")
    return f"(struct.get $h{i} 0 (struct.new $h{i} {build}))"


def c_table(build, ptype, ctx):
    i = ctx.fresh()
    ctx.tables.append(f"  (table $t{i} 1 (ref null {_heap(ptype)}))")
    return (f"(block (result {ptype}) (table.set $t{i} (i32.const 0) {build}) "
            f"(ref.as_non_null (table.get $t{i} (i32.const 0))))")


def c_extern(build, ptype, ctx):
    return f"(ref.cast {ptype} (any.convert_extern (extern.convert_any {build})))"


_CONDUITS = [("tag", c_tag), ("call", c_call), ("global", c_global), ("broncast", c_broncast),
             ("tailcall", c_tailcall), ("field", c_field), ("table", c_table), ("extern", c_extern)]
_BONUS = 100000
_TAGS = ("a", "b", "c")


# ---- SHAPE 1: conduit chain (subsumes castbr / externconvert / eh-value) ----
def _gen_chain(seed):
    rng = random.Random(seed)
    kind, ptype, build, read, val = _payload(seed)
    probe = (seed % 5 == 0)                              # PRESENCE probe: read ref.is_null (0) so a null /
    if probe:                                            # wrong-type forward (the castbr class) is a VALUE diff
        read, val = "(ref.is_null)", 0
    ctx = _Ctx()
    chain = [rng.choice(_CONDUITS) for _ in range(1 + seed % 3)]
    for _name, fn in chain:
        build = fn(build, ptype, ctx)
    stressor = (seed % 3 == 0)
    consumer = "local" if (stressor or (seed // 3) % 2 == 1) else "direct"
    if consumer == "direct":
        consume = f"    {read}"
    else:
        churn = ("\n    (block $cd (loop $cl (br_if $cd (i32.ge_u (local.get $ci) (i32.const 2000))) "
                 "(drop (array.new_default $arr (i32.const 1000))) "
                 "(local.set $ci (i32.add (local.get $ci) (i32.const 1))) (br $cl)))") if stressor else ""
        consume = f"    (local.set $p){churn}\n    (local.get $p) {read}"
    wat = ctx.module(ptype, f"    {build}\n{consume}", f" (local $p {ptype}) (local $ci i32)")
    recipe = "+".join(name for name, _ in chain)
    label = f"compose-{kind}{'-isnull' if probe else ''}-{recipe}{'-gc' if stressor else ''}-{consumer[:3]}"
    return label, "f", f"OK {val}", wat


# ---- SHAPE 2: routed exception nest (subsumes exnstack) ----
def _gen_nest(seed):
    rng = random.Random(seed * 2654435761 & 0xffffffff)
    K = 3 + seed % 6
    kind, ptype, build, read, val = _payload(seed)
    consume = "direct" if (seed // 4) % 2 == 0 else "local"
    subsets = [set(_TAGS)] + [set(rng.sample(_TAGS, rng.randint(1, 3))) for _ in range(1, K)]
    throw_tag = rng.choice(_TAGS)
    catcher = max(i for i in range(K) if throw_tag in subsets[i])
    expected = val + catcher * _BONUS

    def handler(i):
        bonus = f"(i32.const {i * _BONUS}) (i32.add) (return)"
        return f"    {read}\n    {bonus}" if consume == "direct" else f"    (local.set $p) (local.get $p) {read}\n    {bonus}"

    def emit(i):
        catches = " ".join(f"(catch ${t} $L{i})" for t in sorted(subsets[i]))
        inner = f"        (throw ${throw_tag} {build})" if i == K - 1 else emit(i + 1)
        return f"  (block $L{i} (result {ptype})\n    (try_table {catches}\n{inner})\n    (unreachable))\n{handler(i)}"

    ctx = _Ctx()
    ctx.tags = [f"  (tag ${t} (param {ptype}))" for t in _TAGS]
    locals = f" (local $p {ptype})" if consume == "local" else ""
    wat = ctx.module(ptype, emit(0), locals)
    return f"compose-nest-{kind}-{consume[:3]}-{throw_tag}@L{catcher}", "f", f"OK {expected}", wat


# ---- SHAPE 3: exnref capture / carry / re-raise + null-throw trap (subsumes eh non-value cases) ----
def _gen_exn(seed):
    sent = 7000 + (seed * 17) % 2000
    if seed % 3 == 2:                                    # mandated null-throw_ref TRAP
        wat = '(module\n  (func (export "f") (result i32)\n    (throw_ref (ref.null exn)) (i32.const 0)))'
        return f"compose-exn-nulltrap-{seed % 7}", "f", "TRAP", wat
    # capture an exnref carrying `sent`, carry it through a GC struct field (or a global), re-raise, recover.
    via_field = (seed % 3 == 0)
    capture = "catch_ref" if seed % 2 == 0 else "catch_all_ref"
    cap_block = (f"(block $cap (result i32 exnref)\n      (try_table (result i32) (catch_ref $t $cap) "
                 f"(throw $t (i32.const {sent}))) (unreachable))\n    (local.set $ex) (drop)") if capture == "catch_ref" else \
                (f"(block $cap (result exnref)\n      (try_table (catch_all_ref $cap) "
                 f"(throw $t (i32.const {sent}))) (unreachable))\n    (local.set $ex)")
    if via_field:
        carry = "(local.set $ex (struct.get $cell 0 (struct.new $cell (local.get $ex))))"
        cell = "  (type $cell (struct (field (mut exnref))))\n"
    else:
        carry = "(global.set $ge (local.get $ex)) (local.set $ex (ref.as_non_null (global.get $ge)))"
        cell = "  (global $ge (mut (ref null exn)) (ref.null exn))\n"
    wat = (f'(module\n  (tag $t (param i32))\n{cell}'
           f'  (func (export "f") (result i32) (local $ex exnref)\n'
           f'    {cap_block}\n'
           f'    {carry}\n'
           f'    (block $re (result i32)\n'
           f'      (try_table (result i32) (catch $t $re) (throw_ref (local.get $ex)) (unreachable)))))')
    return f"compose-exn-{'field' if via_field else 'global'}-{capture}-{seed % 7}", "f", f"OK {sent}", wat


def compose_gen(seed):
    """Dispatch to one of the three shapes; together they subsume castbr / externconvert / eh / exnstack."""
    s = seed % 5
    if s == 3:
        return _gen_nest(seed)
    if s == 4:
        return _gen_exn(seed)
    return _gen_chain(seed)


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

    print("=== compose: conformant engines must agree across all three shapes ===")
    bad = 0
    for s in range(40):
        label, export, expected, wat = compose_gen(s)
        open("/tmp/cp.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/cp.wat", "-o", "/tmp/cp.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/cp.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:36} exp={expected:8} {row}")
    print(f"\n40 programs, {bad} disagreeing with the oracle")
