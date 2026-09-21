"""Integer boundary algebra: does the engine compute the spec-fixed constant at every NON-trapping edge?

The non-trapping complement of `trapline`: there the law was "this op traps at the boundary"; here every
program's oracle is a baked constant, computed by a Python bit-model, never a TRAP. Three boundary laws:

- a shift / rotate count is taken modulo the bit width — `i32.shl 1 32` is 1 (shift by ZERO), not 0; a count
  of -1 (0xFFFFFFFF) shifts by 31. An engine that masks with `n < width` (skipping the op) or leaks host
  undefined-shift semantics diverges here;
- integer mul / add / sub wrap SILENTLY — `INT_MIN * -1 == INT_MIN` exactly where `INT_MIN / -1` traps
  (the `trapline` pair), `65536 * 65536 == 0`, `INT_MAX + 1 == INT_MIN`. Native arithmetic without wrapping,
  or an INT_OVERFLOW trap where the spec mandates none, is a divergence;
- `clz` / `ctz` of 0 are the width (32 / 64), `popcnt` counts the ones, and `extendN_s` reads ONLY the low
  N bits (384 extend8_s to -128, not 384).

The `_runtime` family routes operands through mutable globals and a memory round-trip first, so the op
executes at run time — a constant folder cannot answer for the runtime shift-mask path. i32 results are
baked signed, i64 as their unsigned 64-bit image; the verdict key compares modulo the width, so either
spelling matches. No second engine required.
"""

_M32 = 0xFFFFFFFF
_M64 = 0xFFFFFFFFFFFFFFFF


def _s32(x):
    """print an i32 the way the engines do: signed decimal."""
    x &= _M32
    return x - 0x100000000 if x & 0x80000000 else x


def _u64(x):
    """print an i64 in nanjet's unsigned image; the verdict key is modulo 2^64 either way."""
    return x & _M64


def _shift32(v):
    """i32 shifts swept ACROSS the width-32 count boundary: the count is `mod 32`, so 32 shifts by zero."""
    op, x, n = [("shl", 1, 31), ("shl", 1, 32), ("shl", 1, 33), ("shl", 1, -1),
                ("shr_u", -1, 32), ("shr_u", -1, 33), ("shr_u", 1, 33),
                ("shr_s", -1, 32), ("shr_s", -8, 33), ("shr_s", 1, 32)][v % 10]
    k = n & 31
    if op == "shl":
        val = _s32(x << k)
    elif op == "shr_u":
        val = _s32((x & _M32) >> k)
    else:
        val = _s32(x) >> k
    wat = ('(module (func (export "f") (result i32)\n'
           f'    (i32.{op} (i32.const {x}) (i32.const {n}))))')
    return f"shift32[{op},{n}]", f"OK {val}", wat, "int"


def _shift64(v):
    """i64 shifts across the width-64 boundary (the count operand is itself an i64, so -1 is 2^64-1)."""
    op, x, n = [("shl", 1, 63), ("shl", 1, 64), ("shl", 1, 65), ("shl", 1, -1),
                ("shr_u", -1, 64), ("shr_u", -8, 65),
                ("shr_s", -1, 64), ("shr_s", -8, 65)][v % 8]
    k = n & 63
    if op == "shl":
        val = _u64(x << k)
    elif op == "shr_u":
        val = _u64((x & _M64) >> k)
    else:  # shr_s: Python's >> is already arithmetic; only an unsigned IMAGE of x needs re-signing
        val = _u64((x if x < 0 else x - 0x10000000000000000 if x & (1 << 63) else x) >> k)
    wat = ('(module (func (export "f") (result i64)\n'
           f'    (i64.{op} (i64.const {x}) (i64.const {n}))))')
    return f"shift64[{op},{n}]", f"OK {val}", wat, "int64"


def _rotate(v):
    """rotl / rotr at and past the width: count mod 32 — 32 is a full turn, 33 turns by one."""
    op, x, n = [("rotl", 1, 31), ("rotl", 1, 32), ("rotl", 1, 33),
                ("rotr", 1, 31), ("rotr", 1, 32), ("rotr", 1, 33),
                ("rotl", 0x12345678, 4), ("rotl", 0x12345678, 36),
                ("rotr", 0x80000000, 1)][v % 9]
    k = n & 31
    val = _s32((((x & _M32) >> k) | (x << (32 - k))) if op == "rotr"
               else (((x << k) & _M32) | ((x & _M32) >> (32 - k))))
    wat = ('(module (func (export "f") (result i32)\n'
           f'    (i32.{op} (i32.const {x}) (i32.const {n}))))')
    return f"rot[{op},{n}]", f"OK {val}", wat, "int"


def _rotate64(v):
    """i64 rotates at the width boundary: 64 is a full turn, 65 rotates by one."""
    op, x, n = [("rotl", 1, 63), ("rotl", 1, 64), ("rotl", 1, 65),
                ("rotr", 1, 63), ("rotr", 1, 64), ("rotr", 1, 65),
                ("rotl", 0x123456789ABCDEF0, 4), ("rotl", 0x123456789ABCDEF0, 68),
                ("rotr", 0x8000000000000000, 1)][v % 9]
    k = n & 63
    val = _u64((((x & _M64) >> k) | (x << (64 - k))) if op == "rotr"
               else (((x << k) & _M64) | ((x & _M64) >> (64 - k))))
    wat = ('(module (func (export "f") (result i64)\n'
           f'    (i64.{op} (i64.const {x}) (i64.const {n}))))')
    return f"rot64[{op},{n}]", f"OK {val}", wat, "int64"


def _clzctz(v):
    """clz / ctz of 0 are the WIDTH (not 0), popcnt counts ones; results are i32 even for i64 operands."""
    w, op, x = [(32, "clz", 0), (32, "clz", 1), (32, "clz", 0x80000000), (32, "clz", -1),
                (32, "ctz", 0), (32, "ctz", 0x80000000), (32, "ctz", 1), (32, "ctz", 12),
                (32, "popcnt", 0), (32, "popcnt", -1), (32, "popcnt", 0x55555555),
                (64, "clz", 0), (64, "ctz", 0), (64, "popcnt", -1), (64, "clz", 1)][v % 15]
    mask = (1 << w) - 1
    xu = x & mask
    if op == "clz":
        val = w if xu == 0 else w - xu.bit_length()
    elif op == "ctz":
        val = w if xu == 0 else (xu & -xu).bit_length() - 1
    else:
        val = bin(xu).count("1")
    wat = ('(module (func (export "f") (result i32)\n'
           f'    (i{w}.{op} (i{w}.const {x}))))')
    return f"clzctz[{op}{w},{x}]", f"OK {val}", wat, "int"


def _mulwrap(v):
    """mul wraps silently at the edges: INT_MIN * -1 == INT_MIN (where div TRAPS), 2^16 * 2^16 == 0."""
    w, a, b = [(32, 2147483647, 2), (32, -2147483648, -1), (32, 65536, 65536),
               (32, 1000000007, 998244353),
               (64, 9223372036854775807, 2), (64, -9223372036854775808, -1),
               (64, 4294967296, 4294967296)][v % 7]
    val = (a * b) & ((1 << w) - 1)
    exp = f"OK {_s32(val)}" if w == 32 else f"OK {val}"
    wat = (f'(module (func (export "f") (result i{w})\n'
           f'    (i{w}.mul (i{w}.const {a}) (i{w}.const {b}))))')
    return f"mulwrap[{w},{a}*{b}]", exp, wat, "int" if w == 32 else "int64"


def _addsub(v):
    """add / sub wrap at INT_MIN / INT_MAX — no trap, no saturation, ever."""
    w, op, a, b = [(32, "add", 2147483647, 1), (32, "add", -2147483648, -1),
                   (32, "sub", -2147483648, 1), (32, "sub", 2147483647, -1),
                   (32, "sub", 0, -2147483648),
                   (64, "add", 9223372036854775807, 1), (64, "add", -9223372036854775808, -1),
                   (64, "sub", -9223372036854775808, 1), (64, "sub", 9223372036854775807, -1)][v % 9]
    val = ((a + b) if op == "add" else (a - b)) & ((1 << w) - 1)
    exp = f"OK {_s32(val)}" if w == 32 else f"OK {val}"
    wat = (f'(module (func (export "f") (result i{w})\n'
           f'    (i{w}.{op} (i{w}.const {a}) (i{w}.const {b}))))')
    return f"addsub[{op}{w},{a},{b}]", exp, wat, "int" if w == 32 else "int64"


def _extend(v):
    """extendN_s reads ONLY the low N bits: 384 -> -128 (0x80), 0x17FFF -> 32767 (0x7FFF)."""
    op, bits, x = [("i32.extend8_s", 8, 128), ("i32.extend8_s", 8, 127), ("i32.extend8_s", 8, 255),
                   ("i32.extend8_s", 8, 384), ("i32.extend8_s", 8, 256),
                   ("i32.extend16_s", 16, 32768), ("i32.extend16_s", 16, 98303), ("i32.extend16_s", 16, 65535),
                   ("i64.extend16_s", 16, 32768), ("i64.extend32_s", 32, 2147483648),
                   ("i64.extend32_s", 32, 4294967295)][v % 11]
    lo = x & ((1 << bits) - 1)
    val = lo - (1 << bits) if lo & (1 << (bits - 1)) else lo
    w = op[1:3]
    exp = f"OK {val}" if w == "32" else f"OK {val & _M64}"
    wat = (f'(module (func (export "f") (result i{w})\n'
           f'    ({op} (i{w}.const {x}))))')  # i64.extendN_s takes an i64 operand, i32's an i32
    return f"extend[{op},{x}]", exp, wat, "int" if w == "32" else "int64"


def _runtime(v):
    """the same edges but UNFOLDABLE: the count rides a mutable global and a memory round-trip, so the
    runtime shift-mask / wrap path must answer, not the constant folder."""
    op, x, n = [("shl", 1, 32), ("shl", 1, 33), ("shr_u", -1, 32), ("shr_s", -8, 33),
                ("rotl", 1, 33), ("rotr", 1, 33), ("extend8_s", 384, 0), ("mul", -2147483648, -1)][v % 8]
    if op == "mul":  # both operands ride storage — the wrap must still be silent
        wat = ('(module (global $ga (mut i32) (i32.const -2147483648)) (global $gb (mut i32) (i32.const -1))\n'
               '  (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (i32.store (i32.const 12) (global.get $gb))\n'
               '    (i32.mul (global.get $ga) (i32.load (i32.const 12)))))')
        return "runtime[mul,INT_MIN*-1]", "OK -2147483648", wat, "int"
    if op == "extend8_s":
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (i32.store (i32.const 8) (i32.const 384))\n'
               '    (i32.extend8_s (i32.load (i32.const 8)))))')
        return "runtime[extend8_s,384]", "OK -128", wat, "int"
    k = n & 31
    if op in ("shl", "shr_u", "shr_s"):
        val = {"shl": _s32(x << k), "shr_u": _s32((x & _M32) >> k), "shr_s": _s32(x) >> k}[op]
    else:
        val = _s32((((x & _M32) >> k) | (x << (32 - k))) if op == "rotr"
                   else (((x << k) & _M32) | ((x & _M32) >> (32 - k))))
    wat = (f'(module (global $gx (mut i32) (i32.const {x})) (global $gn (mut i32) (i32.const {n}))\n'
           '  (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           '    (i32.store (i32.const 8) (global.get $gn))\n'
           f'    (i32.{op} (global.get $gx) (i32.load (i32.const 8)))))')
    return f"runtime[{op},{n}]", f"OK {val}", wat, "int"


_FAMILIES = [_shift32, _shift64, _rotate, _rotate64, _clzctz, _mulwrap, _addsub, _extend, _runtime]


def intedge_gen(seed):
    """Return (label, export, expected, wat, rtype): the seed-th integer-boundary probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat, rtype = fam(seed // len(_FAMILIES))
    return f"intedge-{label}", "f", expected, wat, rtype


if __name__ == "__main__":
    import subprocess, os, re, shutil
    os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("WASMEDGE_LIB", os.path.expanduser("~/wasm-engines/wasmedge/lib"))
    ENG = os.path.expanduser("~/wasm-engines")
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WT = shutil.which("wasmtime") or f"{ENG}/wasmtime-v46/wasmtime"
    WE = shutil.which("wasmedge") or f"{ENG}/wasmedge/bin/wasmedge"
    MCR = f"{REPO}/runner/target/release/mc-runner"
    NODE = os.path.expanduser("~/.nvm/versions/node/v26.3.0/bin/node"); V8 = f"{REPO}/miscast/oracle/v8.js"

    def res(p):
        out = ((p.stdout or "") + (p.stderr or "")).lower()
        if p.returncode != 0 or any(k in out for k in (
                "trap", "unreachable", "out of bounds", "overflow", "invalid conversion", "cast",
                "execution failed", "null", "divide", "runtimeerror")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== intedge: every engine must hit the spec-fixed constant at each non-trapping edge ===")
    bad = 0
    for s in range(88):
        label, export, expected, wat, _rt = intedge_gen(s)
        open("/tmp/ie.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/ie.wat", "-o", "/tmp/ie.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/ie.wasm") for e in ("wt", "we", "mcr", "v8")}
        def ukey(v, w):
            try: return int(v.split()[1], 0) & ((1 << w) - 1)
            except (IndexError, ValueError): return None
        w = 32 if _rt == "int" else 64
        ok = all(ukey(v, w) == ukey(expected, w) for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:34} exp={expected:24} {row}")
    print(f"\n88 programs, {bad} disagreeing")
