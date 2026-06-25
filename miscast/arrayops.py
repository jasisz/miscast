"""Bulk GC array operations: array.copy / array.fill / array.new_data, swept for boundary, overlap, and
element-type variance.

This is the surface Wizard#656 lives on — `array.copy` checking element-type subtyping in the wrong direction
(it rejects a valid *widening* copy and accepts an invalid *narrowing* one). The narrowing (invalid) case is a
single corpus entry in the `invalid` battery; this generator covers the surface SYSTEMATICALLY and
self-checking: build arrays with known values, run the bulk op, read the result back, and compare. A copy that
goes out of bounds must TRAP; an in-bounds copy / fill must leave the exact expected element; an overlapping
copy must behave like memmove; and a VALID widening ref-copy must run (the half Wizard wrongly rejects). No
second engine required — the result is fixed by construction.
"""


def _copy_basic(v):
    idx = v % 4
    vals = [10, 20, 30, 40]
    wat = ('(module (type $a (array (mut i32)))\n'
           '  (func (export "f") (result i32) (local $s (ref $a)) (local $d (ref $a))\n'
           '    (local.set $s (array.new_fixed $a 4 (i32.const 10) (i32.const 20) (i32.const 30) (i32.const 40)))\n'
           '    (local.set $d (array.new_default $a (i32.const 4)))\n'
           '    (array.copy $a $a (local.get $d) (i32.const 0) (local.get $s) (i32.const 0) (i32.const 4))\n'
           f'    (array.get $a (local.get $d) (i32.const {idx}))))')
    return f"copy-basic[{idx}]", f"OK {vals[idx]}", wat


def _copy_partial(v):
    # copy src[1..3) into dst[2..4): dst[2]=src[1]=20, dst[3]=src[2]=30; dst[0] stays 0
    idx, exp = [(2, "OK 20"), (3, "OK 30"), (0, "OK 0")][v % 3]
    wat = ('(module (type $a (array (mut i32)))\n'
           '  (func (export "f") (result i32) (local $s (ref $a)) (local $d (ref $a))\n'
           '    (local.set $s (array.new_fixed $a 4 (i32.const 10) (i32.const 20) (i32.const 30) (i32.const 40)))\n'
           '    (local.set $d (array.new_default $a (i32.const 4)))\n'
           '    (array.copy $a $a (local.get $d) (i32.const 2) (local.get $s) (i32.const 1) (i32.const 2))\n'
           f'    (array.get $a (local.get $d) (i32.const {idx}))))')
    return f"copy-partial[{idx}]", exp, wat


def _copy_oob(v):
    # dst offset 0 + len 5 on a length-4 dst -> out of bounds -> TRAP (the off-by-one is len 4 vs 5)
    length = [4, 5][v % 2]
    exp = "OK 40" if length == 4 else "TRAP"
    wat = ('(module (type $a (array (mut i32)))\n'
           '  (func (export "f") (result i32) (local $s (ref $a)) (local $d (ref $a))\n'
           '    (local.set $s (array.new_fixed $a 4 (i32.const 10) (i32.const 20) (i32.const 30) (i32.const 40)))\n'
           '    (local.set $d (array.new_default $a (i32.const 4)))\n'
           f'    (array.copy $a $a (local.get $d) (i32.const 0) (local.get $s) (i32.const 0) (i32.const {length}))\n'
           '    (array.get $a (local.get $d) (i32.const 3))))')
    return f"copy-oob[len={length}]", exp, wat


def _copy_overlap(v):
    # same array [1,2,3,4]; copy src[0..3) into dst[1..4) (overlap, memmove) -> [1,1,2,3]
    idx, exp = [(1, "OK 1"), (2, "OK 2"), (3, "OK 3")][v % 3]
    wat = ('(module (type $a (array (mut i32)))\n'
           '  (func (export "f") (result i32) (local $x (ref $a))\n'
           '    (local.set $x (array.new_fixed $a 4 (i32.const 1) (i32.const 2) (i32.const 3) (i32.const 4)))\n'
           '    (array.copy $a $a (local.get $x) (i32.const 1) (local.get $x) (i32.const 0) (i32.const 3))\n'
           f'    (array.get $a (local.get $x) (i32.const {idx}))))')
    return f"copy-overlap[{idx}]", exp, wat


def _fill(v):
    # fill x[1..3) with 55; x[2]=55, x[0]=0; a length 5 fill from idx 1 (1+5>4) -> TRAP
    idx, ln, exp = [(2, 2, "OK 55"), (0, 2, "OK 0"), (1, 5, "TRAP")][v % 3]
    wat = ('(module (type $a (array (mut i32)))\n'
           '  (func (export "f") (result i32) (local $x (ref $a))\n'
           '    (local.set $x (array.new_default $a (i32.const 4)))\n'
           f'    (array.fill $a (local.get $x) (i32.const 1) (i32.const 55) (i32.const {ln}))\n'
           f'    (array.get $a (local.get $x) (i32.const {idx}))))')
    return f"fill[idx={idx},len={ln}]", exp, wat


def _new_data(v):
    # array.new_data from a 4-byte data segment {1,2,3,4} as i32 -> a 1-element i32 array holding 0x04030201
    exp = "OK 67305985"   # little-endian 0x04030201
    wat = ('(module (type $a (array (mut i32))) (data $d "\\01\\02\\03\\04")\n'
           '  (func (export "f") (result i32)\n'
           '    (array.get $a (array.new_data $a $d (i32.const 0) (i32.const 1)) (i32.const 0))))')
    return "new-data", exp, wat


def _widening_copy(v):
    # the VALID half of Wizard#656: src elem (ref null $sub) <: dst elem (ref null $sup) -> a widening copy
    # MUST run (read the copied element back through the supertype); Wizard wrongly REJECTS it.
    wat = ('(module\n'
           '  (type $sup (sub (struct (field i32))))\n'
           '  (type $sub (sub $sup (struct (field i32) (field i32))))\n'
           '  (type $asup (array (mut (ref null $sup))))\n'
           '  (type $asub (array (mut (ref null $sub))))\n'
           '  (func (export "f") (result i32) (local $s (ref $asub)) (local $d (ref $asup))\n'
           '    (local.set $s (array.new_fixed $asub 1 (struct.new $sub (i32.const 42) (i32.const 0))))\n'
           '    (local.set $d (array.new_default $asup (i32.const 1)))\n'
           '    (array.copy $asup $asub (local.get $d) (i32.const 0) (local.get $s) (i32.const 0) (i32.const 1))\n'
           '    (struct.get $sup 0 (ref.as_non_null (array.get $asup (local.get $d) (i32.const 0))))))')
    return "widening-copy", "OK 42", wat


def _init_data(v):
    # array.init_data from a 4-element data segment, swept across boundary + zero-length-at-end
    # srcoff is a BYTE offset, len is in ELEMENTS (×4 bytes); the 16-byte segment OOBs at byte offset 12+8
    idx, dst, srcoff, ln, exp = [(2, 1, 0, 2, "OK 2"), (0, 0, 12, 2, "TRAP"),
                                 (0, 2, 0, 2, "TRAP"), (0, 3, 0, 0, "OK 0")][v % 4]
    n = 4 if v % 4 != 2 else 3                           # the dest-OOB variant uses a length-3 array
    wat = ('(module (type $a (array (mut i32)))\n'
           '  (data $d "\\01\\00\\00\\00\\02\\00\\00\\00\\03\\00\\00\\00\\04\\00\\00\\00")\n'
           '  (func (export "f") (result i32) (local $x (ref $a))\n'
           f'    (local.set $x (array.new_default $a (i32.const {n})))\n'
           f'    (array.init_data $a $d (local.get $x) (i32.const {dst}) (i32.const {srcoff}) (i32.const {ln}))\n'
           f'    (array.get $a (local.get $x) (i32.const {idx}))))')
    return f"init-data[dst={dst},off={srcoff},len={ln}]", exp, wat


def _init_elem(v):
    # array.init_elem into a funcref array from a 2-func elem segment; read ref.is_null back
    dst, srcoff, ln, idx, exp = [(0, 0, 2, 0, "OK 0"), (0, 1, 2, 0, "TRAP"), (2, 0, 0, 0, "OK 1")][v % 3]
    n = 4 if v % 3 == 1 else 3 if v % 3 == 0 else 2
    wat = ('(module (type $a (array (mut funcref)))\n'
           '  (func $g (result i32) (i32.const 0)) (elem $e func $g $g)\n'
           '  (func (export "f") (result i32) (local $x (ref $a))\n'
           f'    (local.set $x (array.new_default $a (i32.const {n})))\n'
           f'    (array.init_elem $a $e (local.get $x) (i32.const {dst}) (i32.const {srcoff}) (i32.const {ln}))\n'
           f'    (ref.is_null (array.get $a (local.get $x) (i32.const {idx})))))')
    return f"init-elem[dst={dst},off={srcoff},len={ln}]", exp, wat


def _dropped_segment(v):
    # Wizard#657: a size-0 array.new_data / init_data on a DROPPED passive segment is in bounds (0+0<=0)
    # and must NOT trap; a size-1 access really is OOB and must trap. (Wizard over-traps the zero-length case.)
    kind = v % 4
    if kind == 0:                                        # drop then size-0 new_data -> a zero-length array (len 0)
        body = "    (array.len (array.new_data $a $d (i32.const 0) (i32.const 0)))"
        exp = "OK 0"
    elif kind == 1:                                      # drop then size-1 new_data -> really OOB now -> TRAP
        body = "    (array.len (array.new_data $a $d (i32.const 0) (i32.const 1)))"
        exp = "TRAP"
    elif kind == 2:                                      # non-dropped size-0 new_data control -> len 0
        body = "    (array.len (array.new_data $a $dok (i32.const 0) (i32.const 0)))"
        exp = "OK 0"
    else:                                                # drop then size-0 init_data is a no-op (array unchanged)
        body = ('    (local.set $x (array.new_fixed $a 1 (i32.const 9)))\n'
                '    (array.init_data $a $d (local.get $x) (i32.const 0) (i32.const 0) (i32.const 0))\n'
                '    (array.get $a (local.get $x) (i32.const 0))')
        exp = "OK 9"
    drop = "    (data.drop $d)\n" if kind != 2 else ""
    decl = "  (data $d \"\\01\\02\\03\\04\") (data $dok \"\\01\\02\\03\\04\")\n"
    loc = " (local $x (ref $a))" if kind == 3 else ""
    wat = (f'(module (type $a (array (mut i32)))\n{decl}'
           f'  (func (export "f") (result i32){loc}\n{drop}{body}))')
    return f"dropped-segment[{kind}]", exp, wat


def _packed(v):
    # packed i8 / i16 arrays: array.get_s sign-extends, array.get_u zero-extends
    ty, store, op, exp = [("i8", 255, "get_s", "OK -1"), ("i8", 255, "get_u", "OK 255"),
                          ("i16", 32768, "get_s", "OK -32768"), ("i16", 32768, "get_u", "OK 32768")][v % 4]
    wat = (f'(module (type $a (array (mut {ty})))\n'
           '  (func (export "f") (result i32) (local $x (ref $a))\n'
           '    (local.set $x (array.new_default $a (i32.const 1)))\n'
           f'    (array.set $a (local.get $x) (i32.const 0) (i32.const {store}))\n'
           f'    (array.{op} $a (local.get $x) (i32.const 0))))')
    return f"packed-{ty}-{op}", exp, wat


_FAMILIES = [_copy_basic, _copy_partial, _copy_oob, _copy_overlap, _fill, _new_data, _widening_copy,
             _init_data, _init_elem, _dropped_segment, _packed]


def arrayops_gen(seed):
    """Return (label, export, expected, wat): the seed-th bulk-array-op probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"arrayops-{label}", "f", expected, wat


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
        if p.returncode != 0 or any(k in out for k in ("trap", "out of bounds", "unreachable", "execution failed", "runtimeerror")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== arrayops: bulk array ops self-check — conformant engines hit the baked value or trap ===")
    bad = 0
    for s in range(20):
        label, export, expected, wat = arrayops_gen(s)
        open("/tmp/ao.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/ao.wat", "-o", "/tmp/ao.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/ao.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:24} exp={expected:10} {row}")
    print(f"\n20 programs, {bad} disagreeing")
