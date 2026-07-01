"""Runtime packed-GC storage probes.

`constinit` already checks packed arrays materialised from segments at instantiation time. This generator
targets the runtime path instead: mutable packed `struct` fields and `array` elements, sign/zero extension,
truncation on write, overlap order for `array.copy`, fill/set visibility, and subtype views over packed
fields. Every program is a normal single-module `.wasm` export with a baked integer oracle.
"""


def _struct_i8_set_get_s(v):
    value = [0x7F, 0x80, 0xFF, 0x1234][v % 4]
    exp = [127, -128, -1, 52][v % 4]
    wat = f"""(module
  (type $s (sub (struct (field (mut i8)))))
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x (struct.new $s (i32.const 0)))
    (struct.set $s 0 (local.get $x) (i32.const {value}))
    (struct.get_s $s 0 (local.get $x))))"""
    return f"struct-i8-set-get_s[{value:#x}]", f"OK {exp}", wat


def _struct_i8_set_get_u(v):
    value = [0x80, 0xFF, -1, 0x1234][v % 4]
    exp = [128, 255, 255, 52][v % 4]
    wat = f"""(module
  (type $s (sub (struct (field (mut i8)))))
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x (struct.new $s (i32.const 0)))
    (struct.set $s 0 (local.get $x) (i32.const {value}))
    (struct.get_u $s 0 (local.get $x))))"""
    return f"struct-i8-set-get_u[{value:#x}]", f"OK {exp}", wat


def _struct_i16_set_get_s(v):
    value = [0x7FFF, 0x8000, 0xFFFF, 0x123456][v % 4]
    exp = [32767, -32768, -1, 0x3456][v % 4]
    wat = f"""(module
  (type $s (sub (struct (field (mut i16)))))
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x (struct.new $s (i32.const 0)))
    (struct.set $s 0 (local.get $x) (i32.const {value}))
    (struct.get_s $s 0 (local.get $x))))"""
    return f"struct-i16-set-get_s[{value:#x}]", f"OK {exp}", wat


def _struct_i16_set_get_u(v):
    value = [0x8000, 0xFFFF, -1, 0x123456][v % 4]
    exp = [32768, 65535, 65535, 0x3456][v % 4]
    wat = f"""(module
  (type $s (sub (struct (field (mut i16)))))
  (func (export "f") (result i32) (local $x (ref $s))
    (local.set $x (struct.new $s (i32.const 0)))
    (struct.set $s 0 (local.get $x) (i32.const {value}))
    (struct.get_u $s 0 (local.get $x))))"""
    return f"struct-i16-set-get_u[{value:#x}]", f"OK {exp}", wat


def _struct_packed_subtype_view(v):
    field_ty, get_op, value, exp = [
        ("i8", "struct.get_s", 0x80, -128),
        ("i16", "struct.get_s", 0x8000, -32768),
        ("i8", "struct.get_u", 0xFF, 255),
        ("i16", "struct.get_u", 0xFFFF, 65535),
    ][v % 4]
    wat = f"""(module
  (type $base (sub (struct (field (mut {field_ty})))))
  (type $sub (sub $base (struct (field (mut {field_ty})) (field i32))))
  (func (export "f") (result i32) (local $x (ref $sub)) (local $b (ref $base))
    (local.set $x (struct.new $sub (i32.const 0) (i32.const 99)))
    (local.set $b (local.get $x))
    (struct.set $base 0 (local.get $b) (i32.const {value}))
    ({get_op} $sub 0 (local.get $x))))"""
    return f"struct-packed-subtype-view[{field_ty},{get_op},{value:#x}]", f"OK {exp}", wat


def _array_i8_set_fill(v):
    fill = [0x80, 0xFF, -1, 0x1234][v % 4]
    exp_mid_u = [128, 255, 255, 52][v % 4]
    wat = f"""(module
  (type $a (array (mut i8)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 4)))
    (array.set $a (local.get $a) (i32.const 0) (i32.const 7))
    (array.fill $a (local.get $a) (i32.const 1) (i32.const {fill}) (i32.const 2))
    (array.set $a (local.get $a) (i32.const 3) (i32.const 9))
    (i32.add
      (i32.add (array.get_u $a (local.get $a) (i32.const 0))
               (array.get_u $a (local.get $a) (i32.const 1)))
      (i32.add (array.get_u $a (local.get $a) (i32.const 2))
               (array.get_u $a (local.get $a) (i32.const 3))))))"""
    return f"array-i8-set-fill[{fill:#x}]", f"OK {16 + 2 * exp_mid_u}", wat


def _array_i16_set_fill(v):
    fill = [0x8000, 0xFFFF, -1, 0x123456][v % 4]
    exp_mid_u = [32768, 65535, 65535, 0x3456][v % 4]
    wat = f"""(module
  (type $a (array (mut i16)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 4)))
    (array.set $a (local.get $a) (i32.const 0) (i32.const 17))
    (array.fill $a (local.get $a) (i32.const 1) (i32.const {fill}) (i32.const 2))
    (array.set $a (local.get $a) (i32.const 3) (i32.const 19))
    (i32.add
      (i32.add (array.get_u $a (local.get $a) (i32.const 0))
               (array.get_u $a (local.get $a) (i32.const 1)))
      (i32.add (array.get_u $a (local.get $a) (i32.const 2))
               (array.get_u $a (local.get $a) (i32.const 3))))))"""
    return f"array-i16-set-fill[{fill:#x}]", f"OK {36 + 2 * exp_mid_u}", wat


def _array_i8_copy_overlap_forward(_v):
    wat = """(module
  (type $a (array (mut i8)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a
      (array.new_fixed $a 5
        (i32.const 1) (i32.const 0x80) (i32.const 3) (i32.const 4) (i32.const 5)))
    (array.copy $a $a (local.get $a) (i32.const 2) (local.get $a) (i32.const 0) (i32.const 3))
    (i32.add
      (array.get_s $a (local.get $a) (i32.const 3))
      (array.get_u $a (local.get $a) (i32.const 4)))))"""
    return "array-i8-copy-overlap-forward", "OK -125", wat


def _array_i8_copy_overlap_backward(_v):
    wat = """(module
  (type $a (array (mut i8)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a
      (array.new_fixed $a 5
        (i32.const 1) (i32.const 0x80) (i32.const 3) (i32.const 4) (i32.const 5)))
    (array.copy $a $a (local.get $a) (i32.const 0) (local.get $a) (i32.const 2) (i32.const 3))
    (i32.add
      (array.get_u $a (local.get $a) (i32.const 0))
      (array.get_u $a (local.get $a) (i32.const 2)))))"""
    return "array-i8-copy-overlap-backward", "OK 8", wat


def _array_i16_copy_overlap_forward(_v):
    wat = """(module
  (type $a (array (mut i16)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a
      (array.new_fixed $a 5
        (i32.const 1) (i32.const 0x8000) (i32.const 3) (i32.const 4) (i32.const 5)))
    (array.copy $a $a (local.get $a) (i32.const 2) (local.get $a) (i32.const 0) (i32.const 3))
    (i32.add
      (array.get_s $a (local.get $a) (i32.const 3))
      (array.get_u $a (local.get $a) (i32.const 4)))))"""
    return "array-i16-copy-overlap-forward", "OK -32765", wat


def _array_i16_copy_overlap_backward(_v):
    wat = """(module
  (type $a (array (mut i16)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a
      (array.new_fixed $a 5
        (i32.const 1) (i32.const 0x8000) (i32.const 3) (i32.const 4) (i32.const 5)))
    (array.copy $a $a (local.get $a) (i32.const 0) (local.get $a) (i32.const 2) (i32.const 3))
    (i32.add
      (array.get_u $a (local.get $a) (i32.const 0))
      (array.get_u $a (local.get $a) (i32.const 2)))))"""
    return "array-i16-copy-overlap-backward", "OK 8", wat


def _packed_struct_array_mix(v):
    """Store a packed value through an array alias, move it into a struct, and read it with the opposite
    signedness. This catches engines that implement packed structs and packed arrays through separate paths."""
    ty, value, get_op, exp = [
        ("i8", 0x80, "struct.get_u", 128),
        ("i8", 0xFF, "struct.get_s", -1),
        ("i16", 0x8000, "struct.get_s", -32768),
        ("i16", 0xFFFF, "struct.get_u", 65535),
    ][v % 4]
    wat = f"""(module
  (type $s (sub (struct (field (mut {ty})))))
  (type $a (array (mut {ty})))
  (func (export "f") (result i32) (local $s (ref $s)) (local $a (ref $a))
    (local.set $s (struct.new $s (i32.const 0)))
    (local.set $a (array.new_default $a (i32.const 2)))
    (array.set $a (local.get $a) (i32.const 1) (i32.const {value}))
    (struct.set $s 0 (local.get $s) (array.get_u $a (local.get $a) (i32.const 1)))
    ({get_op} $s 0 (local.get $s))))"""
    return f"packed-struct-array-mix[{ty},{value:#x}]", f"OK {exp}", wat


_FAMILIES = [_struct_i8_set_get_s, _struct_i8_set_get_u, _struct_i16_set_get_s, _struct_i16_set_get_u,
             _struct_packed_subtype_view, _array_i8_set_fill, _array_i16_set_fill,
             _array_i8_copy_overlap_forward, _array_i8_copy_overlap_backward,
             _array_i16_copy_overlap_forward, _array_i16_copy_overlap_backward,
             _packed_struct_array_mix]


def packedops_gen(seed):
    """Return (label, export, expected, wat): the seed-th packed runtime storage probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"packedops-{label}", "f", expected, wat


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

    print("=== packedops: runtime packed GC storage sign/zero/trunc/copy probes ===")
    bad = 0
    for s in range(4 * len(_FAMILIES)):
        label, export, expected, wat = packedops_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/po.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "gc=y", "--invoke", export, "/tmp/po.wasm"],
                                capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:48} exp={expected:9} wasmtime={got}")
        else:
            print(f"  OK  {label:48} assembles")
    print(f"\n{4 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
