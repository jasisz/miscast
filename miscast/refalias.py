"""Reference-identity / alias-preservation generator.

Most value-preservation probes read a field after moving a GC reference through some mechanism. An engine
could still pass those while silently cloning or substituting an object with the same contents. This generator
checks the stronger law: paths that preserve a reference must preserve its identity, observed with `ref.eq`.
Negative controls create equal-looking but fresh objects and must return 0.
"""

_TYPES = """  (type $s (sub (struct (field i32))))
  (type $box (sub (struct (field (ref null $s)) (field (ref null $s)))))
  (type $arr (array (mut (ref null $s))))
"""


def _obj(value):
    return f"(struct.new $s (i32.const {value}))"


def _fresh_not_alias(v):
    val = 20 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32)
    (ref.eq {_obj(val)} {_obj(val)})))"""
    return "fresh-equal-shape-not-alias", "OK 0", wat


def _field_alias(v):
    val = 60 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $s)) (local $b (ref $box))
    (local.set $x {_obj(val)})
    (local.set $b (struct.new $box (local.get $x) (local.get $x)))
    (ref.eq
      (struct.get $box 0 (local.get $b))
      (struct.get $box 1 (local.get $b)))))"""
    return "struct-two-fields-alias", "OK 1", wat


def _field_not_alias(v):
    val = 70 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $b (ref $box))
    (local.set $b (struct.new $box {_obj(val)} {_obj(val)}))
    (ref.eq
      (struct.get $box 0 (local.get $b))
      (struct.get $box 1 (local.get $b)))))"""
    return "struct-two-fields-not-alias", "OK 0", wat


def _array_copy_alias(v):
    val = 80 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $s)) (local $a (ref $arr))
    (local.set $x {_obj(val)})
    (local.set $a (array.new_default $arr (i32.const 2)))
    (array.set $arr (local.get $a) (i32.const 0) (local.get $x))
    (array.copy $arr $arr (local.get $a) (i32.const 1) (local.get $a) (i32.const 0) (i32.const 1))
    (ref.eq
      (array.get $arr (local.get $a) (i32.const 0))
      (array.get $arr (local.get $a) (i32.const 1)))))"""
    return "array-copy-preserves-alias", "OK 1", wat


def _array_copy_not_alias(v):
    val = 90 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $a (ref $arr))
    (local.set $a (array.new_fixed $arr 2 {_obj(val)} {_obj(val)}))
    (ref.eq
      (array.get $arr (local.get $a) (i32.const 0))
      (array.get $arr (local.get $a) (i32.const 1)))))"""
    return "array-fresh-slots-not-alias", "OK 0", wat


def _table_copy_alias(v):
    val = 100 + v
    wat = f"""(module
{_TYPES}
  (table $t 2 (ref null $s))
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x {_obj(val)})
    (table.set $t (i32.const 0) (local.get $x))
    (table.copy $t $t (i32.const 1) (i32.const 0) (i32.const 1))
    (ref.eq (table.get $t (i32.const 0)) (table.get $t (i32.const 1)))))"""
    return "table-copy-preserves-alias", "OK 1", wat


def _table_fill_alias(v):
    val = 110 + v
    wat = f"""(module
{_TYPES}
  (table $t 2 (ref null $s))
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x {_obj(val)})
    (table.fill $t (i32.const 0) (local.get $x) (i32.const 2))
    (ref.eq (table.get $t (i32.const 0)) (table.get $t (i32.const 1)))))"""
    return "table-fill-preserves-alias", "OK 1", wat


def _extern_roundtrip_alias(v):
    val = 120 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x {_obj(val)})
    (ref.eq
      (local.get $x)
      (ref.cast (ref $s) (any.convert_extern (extern.convert_any (local.get $x)))))))"""
    return "extern-roundtrip-alias", "OK 1", wat


def _br_on_cast_alias(v):
    val = 130 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $s)) (local $y (ref $s))
    (local.set $x {_obj(val)})
    (local.set $y
      (block $ok (result (ref $s))
        (br_on_cast $ok (ref eq) (ref $s) (local.get $x))
        (unreachable)))
    (ref.eq (local.get $x) (local.get $y))))"""
    return "br_on_cast-preserves-alias", "OK 1", wat


def _try_table_alias(v):
    val = 140 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $s)))
  (func (export "f") (result i32) (local $x (ref $s)) (local $y (ref $s))
    (local.set $x {_obj(val)})
    (local.set $y
      (block $h (result (ref $s))
        (try_table (catch $e $h)
          (throw $e (local.get $x)))
        (unreachable)))
    (ref.eq (local.get $x) (local.get $y))))"""
    return "try_table-catch-preserves-alias", "OK 1", wat


def _throw_ref_alias(v):
    val = 150 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $s)))
  (func (export "f") (result i32) (local $x (ref $s)) (local $y (ref $s)) (local $ex exnref)
    (local.set $x {_obj(val)})
    (block $cap (result (ref $s) exnref)
      (try_table (catch_ref $e $cap)
        (throw $e (local.get $x)))
      (unreachable))
    (local.set $ex)
    (drop)
    (local.set $y
      (block $h (result (ref $s))
        (try_table (catch $e $h)
          (throw_ref (local.get $ex))
          (unreachable))
        (unreachable)))
    (ref.eq (local.get $x) (local.get $y))))"""
    return "throw_ref-reraises-same-payload", "OK 1", wat


def _call_ref_alias(v):
    val = 160 + v
    wat = f"""(module
{_TYPES}
  (type $id (sub (func (param (ref $s)) (result (ref $s)))))
  (func $id (type $id) (param (ref $s)) (result (ref $s)) (local.get 0))
  (elem declare func $id)
  (func (export "f") (result i32) (local $x (ref $s)) (local $y (ref $s))
    (local.set $x {_obj(val)})
    (local.set $y (call_ref $id (local.get $x) (ref.func $id)))
    (ref.eq (local.get $x) (local.get $y))))"""
    return "call_ref-return-preserves-alias", "OK 1", wat


_FAMILIES = [_fresh_not_alias, _field_alias, _field_not_alias, _array_copy_alias, _array_copy_not_alias,
             _table_copy_alias, _table_fill_alias, _extern_roundtrip_alias, _br_on_cast_alias,
             _try_table_alias, _throw_ref_alias, _call_ref_alias]


def refalias_gen(seed):
    """Return (label, export, expected, wat): the seed-th reference-identity probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"refalias-{label}", "f", expected, wat


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

    print("=== refalias: reference identity must survive every value-preserving path ===")
    bad = 0
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = refalias_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/ra.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,exceptions=y",
                                 "--invoke", export, "/tmp/ra.wasm"], capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:40} exp={expected:5} wasmtime={got}")
        else:
            print(f"  OK  {label:40} assembles")
    print(f"\n{2 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
