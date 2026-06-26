"""GC constant-expression initialiser generator: does the engine validate AND evaluate a GC const-expr
in a global / elem / data init position exactly per the spec?

A different oracle law again — not value-preservation (`compose`), trap-boundary (`trapline`), or
cast-correctness (`castalgebra`), but the correctness of CONSTANT-EXPRESSION evaluation at instantiation
time: a global initialised with struct.new / array.new / ref.i31, optionally with extended-const
arithmetic, then read back; an elem / data segment of GC const-exprs materialised via array.new_elem /
array.new_data / a table. The per-program oracle is the baked value the read must produce. The prize is
an engine that rejects a VALID const-init (Talos#109 rejects a plain struct.new global) or mis-evaluates
/ crashes on one (wasmz panics on ref.i31, errors on extended-const arithmetic). No second engine
required.

The INVALID const-inits (a non-constant operator, a wrong-typed initialiser, a forward reference) are a
validator question — verdict REJECT — and live in the `invalid` battery, not here; this generator is
execution-only and every program is a VALID module carrying a baked answer.
"""


def _global_struct(v):
    """A global initialised with struct.new(consts), read back via struct.get — the Talos#109 surface
    (a plain struct.new global initialiser that is wrongly rejected)."""
    kind = v % 3
    if kind == 0:
        wat = ('(module\n'
               '  (type $s (sub (struct (field i32))))\n'
               '  (global $g (ref $s) (struct.new $s (i32.const 5)))\n'
               '  (func (export "f") (result i32) (struct.get $s 0 (global.get $g))))')
        return "global-struct[single]", "OK 5", wat
    if kind == 1:
        wat = ('(module\n'
               '  (type $s (sub (struct (field i32))))\n'
               '  (global $g (mut (ref $s)) (struct.new $s (i32.const 7)))\n'
               '  (func (export "f") (result i32) (struct.get $s 0 (global.get $g))))')
        return "global-struct[mutable]", "OK 7", wat
    wat = ('(module\n'
           '  (type $s (sub (struct (field i32) (field i32))))\n'
           '  (global $g (ref $s) (struct.new $s (i32.const 11) (i32.const 22)))\n'
           '  (func (export "f") (result i32) (struct.get $s 1 (global.get $g))))')
    return "global-struct[two-field]", "OK 22", wat


def _global_i31(v):
    """A global initialised with ref.i31, read signed — including the 31-bit sign-bit edge. (wasmz
    panics on a ref.i31 const-expr global.)"""
    val, exp, name = [(42, 42, "small"),
                      (1073741824, -1073741824, "sign-bit"),   # 2^30 sets bit 30 -> get_s is negative
                      (-7, -7, "negative")][v % 3]
    wat = ('(module\n'
           f'  (global $g (ref i31) (ref.i31 (i32.const {val})))\n'
           '  (func (export "f") (result i32) (i31.get_s (global.get $g))))')
    return f"global-i31[{name}]", f"OK {exp}", wat


def _global_array(v):
    """A global initialised with array.new_fixed (read an element) or array.new_default (read a zero)."""
    if v % 2 == 0:
        wat = ('(module\n'
               '  (type $a (sub (array i32)))\n'
               '  (global $g (ref $a) (array.new_fixed $a 3 (i32.const 10) (i32.const 20) (i32.const 30)))\n'
               '  (func (export "f") (result i32) (array.get $a (global.get $g) (i32.const 2))))')
        return "global-array[fixed]", "OK 30", wat
    wat = ('(module\n'
           '  (type $a (sub (array i32)))\n'
           '  (global $g (ref $a) (array.new_default $a (i32.const 4)))\n'
           '  (func (export "f") (result i32) (array.get $a (global.get $g) (i32.const 1))))')
    return "global-array[default-zero]", "OK 0", wat


def _extended_const(v):
    """Extended-const arithmetic INSIDE a GC const-expr — i32.add/sub/mul folded at instantiation,
    inside struct.new / array.new_fixed / ref.i31. (wasmz errors / panics on the arithmetic opcode.)"""
    kind = v % 4
    if kind == 0:
        wat = ('(module\n'
               '  (type $s (sub (struct (field i32))))\n'
               '  (global $g (ref $s) (struct.new $s (i32.add (i32.const 50) (i32.const 50))))\n'
               '  (func (export "f") (result i32) (struct.get $s 0 (global.get $g))))')
        return "extconst[add-in-struct]", "OK 100", wat
    if kind == 1:
        wat = ('(module\n'
               '  (type $a (sub (array i32)))\n'
               '  (global $g (ref $a) (array.new_fixed $a 1 (i32.mul (i32.const 6) (i32.const 7))))\n'
               '  (func (export "f") (result i32) (array.get $a (global.get $g) (i32.const 0))))')
        return "extconst[mul-in-array]", "OK 42", wat
    if kind == 2:
        wat = ('(module\n'
               '  (type $s (sub (struct (field i32))))\n'
               '  (global $g (ref $s) (struct.new $s (i32.sub (i32.const 20) (i32.const 12))))\n'
               '  (func (export "f") (result i32) (struct.get $s 0 (global.get $g))))')
        return "extconst[sub-in-struct]", "OK 8", wat
    wat = ('(module\n'                                  # arithmetic payload inside ref.i31
           '  (global $g (ref i31) (ref.i31 (i32.add (i32.const 100) (i32.const 23))))\n'
           '  (func (export "f") (result i32) (i31.get_s (global.get $g))))')
    return "extconst[add-in-i31]", "OK 123", wat


def _nested(v):
    """A nested GC value built entirely in a const-expr: a struct holding an array, or a struct holding
    a struct — read the inner field."""
    if v % 2 == 0:
        wat = ('(module\n'
               '  (type $arr (sub (array i32)))\n'
               '  (type $box (sub (struct (field (ref $arr)))))\n'
               '  (global $g (ref $box) (struct.new $box (array.new_fixed $arr 2 (i32.const 20) (i32.const 30))))\n'
               '  (func (export "f") (result i32)\n'
               '    (array.get $arr (struct.get $box 0 (global.get $g)) (i32.const 0))))')
        return "nested[struct-of-array]", "OK 20", wat
    wat = ('(module\n'
           '  (type $inner (sub (struct (field i32))))\n'
           '  (type $outer (sub (struct (field (ref $inner)))))\n'
           '  (global $g (ref $outer) (struct.new $outer (struct.new $inner (i32.const 9))))\n'
           '  (func (export "f") (result i32)\n'
           '    (struct.get $inner 0 (struct.get $outer 0 (global.get $g)))))')
    return "nested[struct-of-struct]", "OK 9", wat


def _elem(v):
    """An elem segment of GC const-exprs, materialised via array.new_elem (struct / i31 elements) or read
    through a table (struct elements) — and a funcref elem proven non-null."""
    kind = v % 3
    if kind == 0:        # struct elements -> array.new_elem -> read a field
        wat = ('(module\n'
               '  (type $box (sub (struct (field i32))))\n'
               '  (type $arr (array (ref $box)))\n'
               '  (elem $e (ref $box)\n'
               '    (item (struct.new $box (i32.const 11)))\n'
               '    (item (struct.new $box (i32.const 22)))\n'
               '    (item (struct.new $box (i32.const 33))))\n'
               '  (func (export "f") (result i32)\n'
               '    (struct.get $box 0 (array.get $arr (array.new_elem $arr $e (i32.const 0) (i32.const 3)) (i32.const 2)))))')
        return "elem[struct]", "OK 33", wat
    if kind == 1:        # i31 elements -> array.new_elem -> get_s
        wat = ('(module\n'
               '  (type $i31arr (array (ref i31)))\n'
               '  (elem $e (ref i31)\n'
               '    (item (ref.i31 (i32.const 60)))\n'
               '    (item (ref.i31 (i32.const 70)))\n'
               '    (item (ref.i31 (i32.const 80))))\n'
               '  (func (export "f") (result i32)\n'
               '    (i31.get_s (array.get $i31arr (array.new_elem $i31arr $e (i32.const 0) (i32.const 3)) (i32.const 1)))))')
        return "elem[i31]", "OK 70", wat
    wat = ('(module\n'                                  # struct elements installed into a table -> table.get
           '  (type $box (sub (struct (field i32))))\n'
           '  (table $t 3 (ref $box) (struct.new $box (i32.const 0)))\n'
           '  (elem (table $t) (i32.const 0) (ref $box)\n'
           '    (item (struct.new $box (i32.const 17)))\n'
           '    (item (struct.new $box (i32.const 18)))\n'
           '    (item (struct.new $box (i32.const 19))))\n'
           '  (func (export "f") (result i32) (struct.get $box 0 (table.get $t (i32.const 2)))))')
    return "elem[table]", "OK 19", wat


def _data(v):
    """A data segment materialised into a GC array via array.new_data, read back as bytes."""
    wat = ('(module\n'
           '  (type $bytes (array i8))\n'
           '  (data $d "\\07\\09\\0b\\0d")\n'
           '  (func (export "f") (result i32)\n'
           '    (local $a (ref $bytes))\n'
           '    (local.set $a (array.new_data $bytes $d (i32.const 0) (i32.const 4)))\n'
           '    (i32.add\n'
           '      (array.get_u $bytes (local.get $a) (i32.const 0))\n'
           '      (array.get_u $bytes (local.get $a) (i32.const 3)))))')
    return "data[i8-bytes]", "OK 20", wat       # 0x07 + 0x0d = 7 + 13 = 20


# --- deeper families: i31 sign/zero-extension boundaries, packed i8/i16 data, complex segment slices ---


def _i31edge(v):
    """i31 const-init read at the 31-bit boundaries with BOTH get_s (sign-extend bit 30) and get_u
    (zero-extend) — the exact corner where an engine can confuse signed / unsigned i31 extension."""
    val, op, exp, name = [
        ("0x3fffffff", "i31.get_s", 1073741823, "max31-s"),
        ("0x3fffffff", "i31.get_u", 1073741823, "max31-u"),
        ("0x40000000", "i31.get_s", -1073741824, "bit30-s"),     # 2^30: bit 30 set -> sign-extends negative
        ("0x40000000", "i31.get_u", 1073741824, "bit30-u"),
        ("0x7fffffff", "i31.get_s", -1, "lo31ones-s"),           # low 31 bits all ones -> -1 signed
        ("0x7fffffff", "i31.get_u", 2147483647, "lo31ones-u"),
        ("0x80000001", "i31.get_s", 1, "trunc-s"),               # >= 2^31 truncates to low 31 bits (=1)
        ("0x80000001", "i31.get_u", 1, "trunc-u"),
    ][v % 8]
    wat = (f'(module\n  (global $g (ref i31) (ref.i31 (i32.const {val})))\n'
           f'  (func (export "f") (result i32) ({op} (global.get $g))))')
    return f"i31edge[{name}]", f"OK {exp}", wat


_PACKED = [
    ("i8-get_s-0x80", "OK -128", r'(module (type $a (array i8)) (data $d "\80\7f\ff\01") (func (export "f") (result i32) (array.get_s $a (array.new_data $a $d (i32.const 0) (i32.const 4)) (i32.const 0))))'),
    ("i8-get_u-0x80", "OK 128", r'(module (type $a (array i8)) (data $d "\80\7f\ff\01") (func (export "f") (result i32) (array.get_u $a (array.new_data $a $d (i32.const 0) (i32.const 4)) (i32.const 0))))'),
    ("i8-get_s-0xff", "OK -1", r'(module (type $a (array i8)) (data $d "\80\7f\ff\01") (func (export "f") (result i32) (array.get_s $a (array.new_data $a $d (i32.const 0) (i32.const 4)) (i32.const 2))))'),
    ("i8-get_u-0xff", "OK 255", r'(module (type $a (array i8)) (data $d "\80\7f\ff\01") (func (export "f") (result i32) (array.get_u $a (array.new_data $a $d (i32.const 0) (i32.const 4)) (i32.const 2))))'),
    ("i8-extension-gap", "OK 256", r"""(module
  (type $a (array i8))
  (data $d "\80")
  (func (export "f") (result i32)
    (local $r (ref $a))
    (local.set $r (array.new_data $a $d (i32.const 0) (i32.const 1)))
    (i32.sub (array.get_u $a (local.get $r) (i32.const 0)) (array.get_s $a (local.get $r) (i32.const 0)))))"""),
    ("i16-get_s-0x8000", "OK -32768", r'(module (type $a (array i16)) (data $d "\00\80") (func (export "f") (result i32) (array.get_s $a (array.new_data $a $d (i32.const 0) (i32.const 1)) (i32.const 0))))'),
    ("i16-get_u-0x8000", "OK 32768", r'(module (type $a (array i16)) (data $d "\00\80") (func (export "f") (result i32) (array.get_u $a (array.new_data $a $d (i32.const 0) (i32.const 1)) (i32.const 0))))'),
]


def _packed(v):
    """Packed i8 / i16 arrays materialised from a data segment via array.new_data, read with array.get_s vs
    array.get_u across the sign boundary (0x80, 0xff, 0x8000) — sign- vs zero-extension of sub-word lanes."""
    name, exp, wat = _PACKED[v % len(_PACKED)]
    return f"packed[{name}]", exp, wat


_SEGMENTS = [
    ("data-i8-offset", "OK 80", r'(module (type $arr (array i8)) (data $d "\10\20\30\40\50\ff") (func (export "f") (result i32) (array.get_u $arr (array.new_data $arr $d (i32.const 2) (i32.const 3)) (i32.const 2))))'),
    ("data-i16-offset-signext", "OK -1", r'(module (type $arr (array i16)) (data $d "\01\00\02\00\ff\ff\04\00") (func (export "f") (result i32) (array.get_s $arr (array.new_data $arr $d (i32.const 4) (i32.const 2)) (i32.const 0))))'),
    ("elem-offset-partial-slice", "OK 300", r"""(module
  (type $ft (func (result i32)))
  (type $arr (array (ref $ft)))
  (func $g0 (type $ft) (result i32) (i32.const 100))
  (func $g1 (type $ft) (result i32) (i32.const 200))
  (func $g2 (type $ft) (result i32) (i32.const 300))
  (func $g3 (type $ft) (result i32) (i32.const 400))
  (elem $e (ref $ft) (ref.func $g0) (ref.func $g1) (ref.func $g2) (ref.func $g3))
  (func (export "f") (result i32)
    (call_ref $ft
      (array.get $arr
        (array.new_elem $arr $e (i32.const 1) (i32.const 2))
        (i32.const 1)))))"""),
    ("elem-boundary-last", "OK 44", r"""(module
  (type $ft (func (result i32)))
  (type $arr (array (ref $ft)))
  (func $g0 (type $ft) (result i32) (i32.const 11))
  (func $g1 (type $ft) (result i32) (i32.const 22))
  (func $g2 (type $ft) (result i32) (i32.const 33))
  (func $g3 (type $ft) (result i32) (i32.const 44))
  (elem $e (ref $ft) (ref.func $g0) (ref.func $g1) (ref.func $g2) (ref.func $g3))
  (func (export "f") (result i32)
    (call_ref $ft
      (array.get $arr
        (array.new_elem $arr $e (i32.const 2) (i32.const 2))
        (i32.const 1)))))"""),
    ("elem-single-boundary-slice", "OK 35", r"""(module
  (type $ft (func (result i32)))
  (type $arr (array (ref $ft)))
  (func $g0 (type $ft) (result i32) (i32.const 5))
  (func $g1 (type $ft) (result i32) (i32.const 15))
  (func $g2 (type $ft) (result i32) (i32.const 25))
  (func $g3 (type $ft) (result i32) (i32.const 35))
  (elem $e (ref $ft) (ref.func $g0) (ref.func $g1) (ref.func $g2) (ref.func $g3))
  (func (export "f") (result i32)
    (call_ref $ft
      (array.get $arr
        (array.new_elem $arr $e (i32.const 3) (i32.const 1))
        (i32.const 0)))))"""),
]


def _segments(v):
    """Complex segment materialisation (the Wizard#656/#657 neighbourhood, all in-bounds): array.new_data
    with a byte offset, array.new_elem with a non-zero offset + partial count (a slice) and at the exact
    segment-length boundary."""
    name, exp, wat = _SEGMENTS[v % len(_SEGMENTS)]
    return f"segments[{name}]", exp, wat


_FAMILIES = [_global_struct, _global_i31, _global_array, _extended_const, _nested, _elem, _data,
             _i31edge, _packed, _segments]


def constinit_gen(seed):
    """Return (label, export, expected, wat): the seed-th GC const-init probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"constinit-{label}", "f", expected, wat


if __name__ == "__main__":
    import subprocess, os, re
    os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("WASMEDGE_LIB", os.path.expanduser("~/wasm-engines/wasmedge/lib"))
    ENG = os.path.expanduser("~/wasm-engines")
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WT = f"{ENG}/wasmtime-v46/wasmtime"; WE = f"{ENG}/wasmedge/bin/wasmedge"
    MCR = f"{REPO}/runner/target/release/mc-runner"
    NODE = os.path.expanduser("~/.nvm/versions/node/v26.3.0/bin/node"); V8 = f"{REPO}/miscast/oracle/v8.js"

    def res(p):
        out = ((p.stdout or "") + (p.stderr or "")).lower()
        if p.returncode != 0 or any(k in out for k in (
                "trap", "unreachable", "out of bounds", "execution failed", "null", "runtimeerror", "error")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== constinit: conformant engines must evaluate every GC const-init to its baked value ===")
    bad = 0
    for s in range(80):
        label, export, expected, wat = constinit_gen(s)
        open("/tmp/ci.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/ci.wat", "-o", "/tmp/ci.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/ci.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:30} exp={expected:8} {row}")
    print(f"\n80 programs, {bad} disagreeing")
