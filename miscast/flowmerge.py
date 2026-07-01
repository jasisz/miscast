"""Control-flow merge / stack-polymorphism generator.

This surface attacks the parts of a validator/runtime that compute value types at merge points: `if`,
typed `select`, `br`, `br_table`, `loop`, `try_table`, and the polymorphic stack after `unreachable`. Each
successful program forwards a GC reference whose concrete type is a subtype of the declared merge result,
then reads a baked field. The sharper variants use multi-value labels and concrete function references, where
implementations tend to accidentally reorder stack values or compute the wrong join type. Trap programs
exercise spec-valid dead code that should validate but trap before the dead GC op executes. The oracle is
baked into each program.
"""

_TYPES = """  (type $base (sub (struct (field i32))))
  (type $left (sub $base (struct (field i32) (field i32))))
  (type $right (sub $base (struct (field i32) (field i64))))
"""


def _left_field(value):
    return f"(struct.new $left (i32.const {value}) (i32.const 7))"


def _right_field(value):
    return f"(struct.new $right (i32.const {value}) (i64.const 9))"


def _read(expr):
    return f"(struct.get $base 0 {expr})"


def _if_join(v):
    cond = v % 2
    exp = 100 + v if cond else 200 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read(f'''(if (result (ref $base)) (i32.const {cond})
      (then {_left_field(100 + v)})
      (else {_right_field(200 + v)}))''')}))"""
    return f"if-ref-join[{cond}]", f"OK {exp}", wat


def _select_join(v):
    cond = v % 2
    exp = 100 + v if cond else 200 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read(f'''(select (result (ref $base))
      {_left_field(100 + v)}
      {_right_field(200 + v)}
      (i32.const {cond}))''')}))"""
    return f"select-ref-join[{cond}]", f"OK {exp}", wat


def _block_br_join(v):
    cond = v % 2
    exp = 100 + v if cond else 200 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read(f'''(block $out (result (ref $base))
      (if (i32.const {cond})
        (then (br $out {_left_field(100 + v)})))
      {_right_field(200 + v)})''')}))"""
    return f"block-br-ref-join[{cond}]", f"OK {exp}", wat


def _br_table_join(v):
    idx = v % 3
    val = 300 + v
    concrete = _left_field(val) if v % 2 == 0 else _right_field(val)
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read(f'''(block $out (result (ref $base))
      (block $l0 (result (ref $base))
        (block $l1 (result (ref $base))
          {concrete}
          (i32.const {idx})
          (br_table $l0 $l1 $out))))''')}))"""
    return f"br_table-ref-join[{idx}]", f"OK {val}", wat


def _multi_value_block(v):
    cond = v % 2
    ref_val, tag = (110 + v, 1000) if cond else (210 + v, 2000)
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $r (ref $base)) (local $tag i32)
    (block $out (result (ref $base) i32)
      (if (i32.const {cond})
        (then (br $out {_left_field(110 + v)} (i32.const 1000))))
      {_right_field(210 + v)}
      (i32.const 2000))
    (local.set $tag)
    (local.set $r)
    (i32.add (struct.get $base 0 (local.get $r)) (local.get $tag))))"""
    return f"multi-value-block-join[{cond}]", f"OK {ref_val + tag}", wat


def _multi_value_br_table(v):
    idx = v % 3
    ref_val, tag = 320 + v, 3000 + idx
    concrete = _left_field(ref_val) if v % 2 == 0 else _right_field(ref_val)
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $r (ref $base)) (local $tag i32)
    (block $out (result (ref $base) i32)
      (block $l0 (result (ref $base) i32)
        (block $l1 (result (ref $base) i32)
          {concrete}
          (i32.const {tag})
          (i32.const {idx})
          (br_table $l0 $l1 $out))))
    (local.set $tag)
    (local.set $r)
    (i32.add (struct.get $base 0 (local.get $r)) (local.get $tag))))"""
    return f"multi-value-br_table-join[{idx}]", f"OK {ref_val + tag}", wat


def _loop_br_join(v):
    val = 350 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read(f'''(block $out (result (ref $base))
      (loop $again
        (br_if $out {_left_field(val)} (i32.const 1))
        (br $again))
      (unreachable))''')}))"""
    return "loop-br-ref-join", f"OK {val}", wat


def _nullable_join(v):
    choose_null = v % 2
    exp = "TRAP" if choose_null else f"OK {400 + v}"
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read(f'''(ref.as_non_null
      (if (result (ref null $base)) (i32.const {choose_null})
        (then (ref.null $base))
        (else {_left_field(400 + v)})))''')}))"""
    return f"nullable-join-as_non_null[{choose_null}]", exp, wat


def _func_select_call(v):
    cond = v % 2
    exp = 700 if cond else 701
    wat = f"""(module
  (type $ft (sub (func (result i32))))
  (func $a (type $ft) (result i32) (i32.const 700))
  (func $b (type $ft) (result i32) (i32.const 701))
  (elem declare func $a $b)
  (func (export "f") (result i32)
    (call_ref $ft
      (select (result (ref $ft))
        (ref.func $a)
        (ref.func $b)
        (i32.const {cond})))))"""
    return f"func-select-call_ref[{cond}]", f"OK {exp}", wat


def _try_table_catch(v):
    val = 500 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $base)))
  (func (export "f") (result i32)
    {_read(f'''(block $h (result (ref $base))
      (try_table (catch $e $h)
        (throw $e {_left_field(val)}))
      (unreachable))''')}))"""
    return "try_table-catch-ref-join", f"OK {val}", wat


def _try_table_multi_catch(v):
    throw_a = v % 2
    val = 800 + v if throw_a else 900 + v
    tag = "a" if throw_a else "b"
    payload = _left_field(800 + v) if throw_a else _right_field(900 + v)
    wat = f"""(module
{_TYPES}
  (tag $a (param (ref $base)))
  (tag $b (param (ref $base)))
  (func (export "f") (result i32)
    {_read(f'''(block $out (result (ref $base))
      (block $ha (result (ref $base))
        (block $hb (result (ref $base))
          (try_table (catch $b $hb) (catch $a $ha)
            (throw ${tag} {payload}))
          (unreachable))
        (br $out))
      (br $out))''')}))"""
    return f"try_table-multi-catch[{tag}]", f"OK {val}", wat


def _try_table_catch_ref(v):
    val = 600 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $base)))
  (func (export "f") (result i32) (local $ex exnref)
    (block $h (result (ref $base) exnref)
      (try_table (catch_ref $e $h)
        (throw $e {_left_field(val)}))
      (unreachable))
    (local.set $ex)
    (drop (local.get $ex))
    (struct.get $base 0)))"""
    return "try_table-catch_ref-stack", f"OK {val}", wat


def _try_table_catch_ref_multi_value(v):
    val, tag = 1000 + v, 77 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $base) i32))
  (func (export "f") (result i32) (local $r (ref $base)) (local $tag i32) (local $ex exnref)
    (block $h (result (ref $base) i32 exnref)
      (try_table (catch_ref $e $h)
        (throw $e {_left_field(val)} (i32.const {tag})))
      (unreachable))
    (local.set $ex)
    (local.set $tag)
    (local.set $r)
    (drop (local.get $ex))
    (i32.add (struct.get $base 0 (local.get $r)) (local.get $tag))))"""
    return "try_table-catch_ref-multivalue", f"OK {val + tag}", wat


def _unreachable_gc_pop(_v):
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    (unreachable)
    (struct.get $base 0)))"""
    return "unreachable-polymorphic-struct_get", "TRAP", wat


def _unreachable_br_operand(_v):
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    {_read('''(block $out (result (ref $base))
      (unreachable)
      (br $out))''')}))"""
    return "unreachable-polymorphic-br-result", "TRAP", wat


def _unreachable_select(_v):
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    (unreachable)
    (select (result (ref $base)))
    (struct.get $base 0)))"""
    return "unreachable-polymorphic-select", "TRAP", wat


_FAMILIES = [_if_join, _select_join, _block_br_join, _br_table_join, _multi_value_block,
             _multi_value_br_table, _loop_br_join, _nullable_join, _func_select_call,
             _try_table_catch, _try_table_multi_catch, _try_table_catch_ref,
             _try_table_catch_ref_multi_value, _unreachable_gc_pop, _unreachable_br_operand,
             _unreachable_select]


def flowmerge_gen(seed):
    """Return (label, export, expected, wat): the seed-th control-flow merge probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"flowmerge-{label}", "f", expected, wat


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in (
                "trap", "unreachable", "null", "cast", "execution failed", "runtimeerror")):
            return "TRAP"
        nums = re.findall(r"-?\d+", p.stdout or "")
        return "OK " + nums[-1] if nums else "OK _"

    print("=== flowmerge: GC refs through control-flow joins and stack-polymorphic dead code ===")
    bad = 0
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = flowmerge_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/fm.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,exceptions=y",
                                 "--invoke", export, "/tmp/fm.wasm"], capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:42} exp={expected:8} wasmtime={got}")
        else:
            print(f"  OK  {label:42} assembles")
    print(f"\n{2 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
