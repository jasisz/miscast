"""NaN payload preservation probes.

`compose` carries an occasional f32 NaN through generic value-preserving conduits. This generator focuses
that surface: f32 and f64 NaN payload bits are routed through runtime storage and control-flow paths, then
reinterpreted to an i64 integer so the differential compares the exact bit pattern instead of canonicalizing
the result as a floating-point NaN.
"""

_F32 = [0x7F801234, 0x7FC01234, 0x7FBFFFFF, 0xFFC00001]
_F64 = [0x7FF0000000001234, 0x7FF8000000001234, 0x7FFFFFFFFFFFFFFF, 0xFFF8000000000001]


def _i64(n):
    return f"OK {n & 0xffffffffffffffff}"


def _f32_val(bits):
    return f"(f32.reinterpret_i32 (i32.const {bits}))"


def _f64_val(bits):
    return f"(f64.reinterpret_i64 (i64.const {bits}))"


def _ret_f32(expr):
    return f"(i64.extend_i32_u (i32.reinterpret_f32 {expr}))"


def _ret_f64(expr):
    return f"(i64.reinterpret_f64 {expr})"


def _f32_struct(v):
    bits = _F32[v % len(_F32)]
    wat = f"""(module
  (type $s (sub (struct (field f32))))
  (func (export "f") (result i64)
    {_ret_f32(f"(struct.get $s 0 (struct.new $s {_f32_val(bits)}))")}))"""
    return f"f32-struct-field[{bits:#x}]", _i64(bits), wat


def _f64_struct(v):
    bits = _F64[v % len(_F64)]
    wat = f"""(module
  (type $s (sub (struct (field f64))))
  (func (export "f") (result i64)
    {_ret_f64(f"(struct.get $s 0 (struct.new $s {_f64_val(bits)}))")}))"""
    return f"f64-struct-field[{bits:#x}]", _i64(bits), wat


def _f32_array_copy(v):
    a, b, c = _F32[v % 4], _F32[(v + 1) % 4], _F32[(v + 2) % 4]
    wat = f"""(module
  (type $a (array (mut f32)))
  (func (export "f") (result i64) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 4
      {_f32_val(a)} {_f32_val(b)} {_f32_val(c)} (f32.const 1)))
    (array.copy $a $a (local.get $a) (i32.const 1) (local.get $a) (i32.const 0) (i32.const 3))
    {_ret_f32("(array.get $a (local.get $a) (i32.const 2))")}))"""
    return f"f32-array-copy-overlap[{b:#x}]", _i64(b), wat


def _f64_array_copy(v):
    a, b, c = _F64[v % 4], _F64[(v + 1) % 4], _F64[(v + 2) % 4]
    wat = f"""(module
  (type $a (array (mut f64)))
  (func (export "f") (result i64) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 4
      {_f64_val(a)} {_f64_val(b)} {_f64_val(c)} (f64.const 1)))
    (array.copy $a $a (local.get $a) (i32.const 1) (local.get $a) (i32.const 0) (i32.const 3))
    {_ret_f64("(array.get $a (local.get $a) (i32.const 2))")}))"""
    return f"f64-array-copy-overlap[{b:#x}]", _i64(b), wat


def _f32_array_fill(v):
    bits = _F32[v % len(_F32)]
    wat = f"""(module
  (type $a (array (mut f32)))
  (func (export "f") (result i64) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 4)))
    (array.fill $a (local.get $a) (i32.const 1) {_f32_val(bits)} (i32.const 2))
    {_ret_f32("(array.get $a (local.get $a) (i32.const 2))")}))"""
    return f"f32-array-fill[{bits:#x}]", _i64(bits), wat


def _f64_array_fill(v):
    bits = _F64[v % len(_F64)]
    wat = f"""(module
  (type $a (array (mut f64)))
  (func (export "f") (result i64) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 4)))
    (array.fill $a (local.get $a) (i32.const 1) {_f64_val(bits)} (i32.const 2))
    {_ret_f64("(array.get $a (local.get $a) (i32.const 2))")}))"""
    return f"f64-array-fill[{bits:#x}]", _i64(bits), wat


def _f32_global_select(v):
    lhs, rhs = _F32[v % 4], _F32[(v + 1) % 4]
    cond = v & 1
    exp = lhs if cond else rhs
    wat = f"""(module
  (global $g (mut f32) (f32.const 0))
  (func (export "f") (result i64)
    (global.set $g
      (select (result f32) {_f32_val(lhs)} {_f32_val(rhs)} (i32.const {cond})))
    {_ret_f32("(global.get $g)")}))"""
    return f"f32-global-select[{cond},{exp:#x}]", _i64(exp), wat


def _f64_global_select(v):
    lhs, rhs = _F64[v % 4], _F64[(v + 1) % 4]
    cond = v & 1
    exp = lhs if cond else rhs
    wat = f"""(module
  (global $g (mut f64) (f64.const 0))
  (func (export "f") (result i64)
    (global.set $g
      (select (result f64) {_f64_val(lhs)} {_f64_val(rhs)} (i32.const {cond})))
    {_ret_f64("(global.get $g)")}))"""
    return f"f64-global-select[{cond},{exp:#x}]", _i64(exp), wat


def _f32_tag_catch(v):
    bits = _F32[v % len(_F32)]
    wat = f"""(module
  (tag $e (param f32))
  (func (export "f") (result i64)
    {_ret_f32(f'''(block $h (result f32)
      (try_table (catch $e $h)
        (throw $e {_f32_val(bits)}))
      (unreachable))''')}))"""
    return f"f32-tag-catch[{bits:#x}]", _i64(bits), wat


def _f64_tag_catch(v):
    bits = _F64[v % len(_F64)]
    wat = f"""(module
  (tag $e (param f64))
  (func (export "f") (result i64)
    {_ret_f64(f'''(block $h (result f64)
      (try_table (catch $e $h)
        (throw $e {_f64_val(bits)}))
      (unreachable))''')}))"""
    return f"f64-tag-catch[{bits:#x}]", _i64(bits), wat


def _f32_call_ref(v):
    bits = _F32[v % len(_F32)]
    wat = f"""(module
  (type $ft (sub (func (result f32))))
  (func $g (type $ft) (result f32) {_f32_val(bits)})
  (elem declare func $g)
  (func (export "f") (result i64)
    {_ret_f32("(call_ref $ft (ref.func $g))")}))"""
    return f"f32-call_ref[{bits:#x}]", _i64(bits), wat


def _f64_call_ref(v):
    bits = _F64[v % len(_F64)]
    wat = f"""(module
  (type $ft (sub (func (result f64))))
  (func $g (type $ft) (result f64) {_f64_val(bits)})
  (elem declare func $g)
  (func (export "f") (result i64)
    {_ret_f64("(call_ref $ft (ref.func $g))")}))"""
    return f"f64-call_ref[{bits:#x}]", _i64(bits), wat


def _f32_memory(v):
    bits = _F32[v % len(_F32)]
    wat = f"""(module
  (memory 1)
  (func (export "f") (result i64)
    (f32.store offset=3 (i32.const 0) {_f32_val(bits)})
    {_ret_f32("(f32.load offset=3 (i32.const 0))")}))"""
    return f"f32-memory-unaligned[{bits:#x}]", _i64(bits), wat


def _f64_memory(v):
    bits = _F64[v % len(_F64)]
    wat = f"""(module
  (memory 1)
  (func (export "f") (result i64)
    (f64.store offset=5 (i32.const 0) {_f64_val(bits)})
    {_ret_f64("(f64.load offset=5 (i32.const 0))")}))"""
    return f"f64-memory-unaligned[{bits:#x}]", _i64(bits), wat


def _f32_signop(v):
    bits = _F32[v % len(_F32)]
    op = ["neg", "abs", "copysign-neg", "copysign-pos"][v % 4]
    if op == "neg":
        expr, exp = f"(f32.neg {_f32_val(bits)})", bits ^ 0x80000000
    elif op == "abs":
        expr, exp = f"(f32.abs {_f32_val(bits)})", bits & 0x7fffffff
    elif op == "copysign-neg":
        expr, exp = f"(f32.copysign {_f32_val(bits)} (f32.const -1))", bits | 0x80000000
    else:
        expr, exp = f"(f32.copysign {_f32_val(bits)} (f32.const 1))", bits & 0x7fffffff
    wat = f"""(module
  (func (export "f") (result i64)
    {_ret_f32(expr)}))"""
    return f"f32-signop-{op}[{bits:#x}]", _i64(exp), wat


def _f64_signop(v):
    bits = _F64[v % len(_F64)]
    op = ["neg", "abs", "copysign-neg", "copysign-pos"][v % 4]
    if op == "neg":
        expr, exp = f"(f64.neg {_f64_val(bits)})", bits ^ 0x8000000000000000
    elif op == "abs":
        expr, exp = f"(f64.abs {_f64_val(bits)})", bits & 0x7fffffffffffffff
    elif op == "copysign-neg":
        expr, exp = f"(f64.copysign {_f64_val(bits)} (f64.const -1))", bits | 0x8000000000000000
    else:
        expr, exp = f"(f64.copysign {_f64_val(bits)} (f64.const 1))", bits & 0x7fffffffffffffff
    wat = f"""(module
  (func (export "f") (result i64)
    {_ret_f64(expr)}))"""
    return f"f64-signop-{op}[{bits:#x}]", _i64(exp), wat


def _f32_br_table(v):
    bits = _F32[v % len(_F32)]
    idx = v % 3
    wat = f"""(module
  (func (export "f") (result i64)
    {_ret_f32(f'''(block $out (result f32)
      (block $a (result f32)
        (block $b (result f32)
          {_f32_val(bits)}
          (i32.const {idx})
          (br_table $a $b $out))))''')}))"""
    return f"f32-br_table[{idx},{bits:#x}]", _i64(bits), wat


def _f64_br_table(v):
    bits = _F64[v % len(_F64)]
    idx = v % 3
    wat = f"""(module
  (func (export "f") (result i64)
    {_ret_f64(f'''(block $out (result f64)
      (block $a (result f64)
        (block $b (result f64)
          {_f64_val(bits)}
          (i32.const {idx})
          (br_table $a $b $out))))''')}))"""
    return f"f64-br_table[{idx},{bits:#x}]", _i64(bits), wat


_FAMILIES = [_f32_struct, _f64_struct, _f32_array_copy, _f64_array_copy, _f32_array_fill, _f64_array_fill,
             _f32_global_select, _f64_global_select, _f32_tag_catch, _f64_tag_catch, _f32_call_ref,
             _f64_call_ref, _f32_memory, _f64_memory, _f32_signop, _f64_signop, _f32_br_table, _f64_br_table]


def nanjet_gen(seed):
    """Return (label, export, expected, wat): the seed-th NaN bit-preservation probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"nanjet-{label}", "f", expected, wat


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in ("trap", "unreachable", "runtimeerror")):
            return "TRAP"
        out = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
        nums = re.findall(r"-?\d+", out[-1] if out else "")
        return "OK " + nums[-1] if nums else "OK _"

    def same_i64(got, expected):
        try:
            return (int(got.split()[1], 0) & 0xffffffffffffffff) == (int(expected.split()[1], 0) & 0xffffffffffffffff)
        except (IndexError, ValueError):
            return False

    print("=== nanjet: f32/f64 NaN payload bit preservation through runtime paths ===")
    bad = 0
    for s in range(4 * len(_FAMILIES)):
        label, export, expected, wat = nanjet_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/nj.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,exceptions=y,tail-call=y",
                                 "--invoke", export, "/tmp/nj.wasm"], capture_output=True, text=True)
            got = res(wt)
            ok = same_i64(got, expected)
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:52} exp={expected:24} wasmtime={got}")
        else:
            print(f"  OK  {label:52} assembles")
    print(f"\n{4 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
