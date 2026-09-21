"""Effective-address arithmetic: `ea = offset + addr` is computed WITHOUT wrap — a near-2^32 offset must push the access out of bounds, never alias back to the wrapped byte.

The sibling of `memory64` (there a 64-bit ADDRESS was truncated; here the SUM of the two u32 addends is),
but it attacks plain 32-bit memories, so every engine runs it with no proposal flags. The spec computes
the effective address exactly — `ea` can need 33 bits — and bounds-checks `ea + N/8 <= len(mem)`. An
engine that forms `ea` in a u32 (or folds the offset into the address register before widening) wraps
`0xFFFFFFFF + 1` down to `0` and reads / writes the bottom of the memory: a sandbox escape. The
`wrapload` / `wrapstore` families plant a sentinel exactly where a wrapping engine would land, so the
bug shows as a returned value where the law mandates TRAP; `edge` sweeps the last-byte boundary with BOTH
operands near it; `widths` does it per access width (8/16/32/64); `bigoffset` checks that a large but
legal offset in a 2-page memory reads fine and steps to TRAP exactly at the size. All results are i32, so
an i64-unfriendly CLI printer cannot mask a finding.
"""

_HEX = "0x%X"


def _wrapload(v):
    """the smoking gun: sentinel planted where a 32-bit ea wrap would land; the load must TRAP, not return it."""
    off, addr, tgt = [(0xFFFFFFFF, 1, 0), (0xFFFFFFF0, 0x10, 0), (0x20, 0xFFFFFF00, 0x20),
                      (0x80000000, 0x80000001, 1), (0xFFFFFFF8, 0xC, 4)][v % 5]
    wat = ('(module (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           f'    (i32.store (i32.const {tgt}) (i32.const 0x5A5A5A5A))   ;; sentinel where a u32 wrap would land\n'
           f'    (i32.load offset={_HEX % off} (i32.const {addr}))))')
    return f"wrapload[off={_HEX % off},addr={addr}]", "TRAP", wat


def _wrapstore(v):
    """the write escape: a wrapping store overwrites the sentinel and the read-back returns it; the law says TRAP."""
    off, addr, tgt = [(0xFFFFFFFF, 1, 0), (0xFFFFFFF0, 0x10, 0), (0x20, 0xFFFFFF00, 0x20),
                      (0x80000000, 0x80000001, 1), (0xFFFFFFF8, 0xC, 4)][v % 5]
    wat = ('(module (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           f'    (i32.store (i32.const {tgt}) (i32.const 111111111))\n'
           f'    (i32.store offset={_HEX % off} (i32.const {addr}) (i32.const 222222222))   ;; must trap\n'
           f'    (i32.load (i32.const {tgt}))))')
    return f"wrapstore[off={_HEX % off},addr={addr}]", "TRAP", wat


def _edge(v):
    """i32 load swept across the 1-page boundary with BOTH operands near it: ea = off + addr, last valid is 65532."""
    off, addr, ok = [(65532, 0, True), (65533, 0, False), (65531, 1, True), (65531, 2, False),
                     (0, 65532, True), (0, 65533, False), (32768, 32764, True), (32768, 32765, False),
                     (65535, 1, False), (65536, 0, False)][v % 10]
    if ok:  # plant the sentinel through the very same (off, addr) the load uses — in-bounds by construction
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               f'    (i32.store offset={off} (i32.const {addr}) (i32.const 4242))\n'
               f'    (i32.load offset={off} (i32.const {addr}))))')
        return f"edge[{off}+{addr}]", "OK 4242", wat
    wat = ('(module (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           f'    (i32.store (i32.const 0) (i32.const 4242))\n'
           f'    (i32.load offset={off} (i32.const {addr}))))')
    return f"edge[{off}+{addr}]", "TRAP", wat


def _widths(v):
    """each access width at its own last valid slot: 8→65535, 16→65534, 32→65532, 64→65528; +1 byte is a TRAP."""
    op, w, off, ok = [("load8_u", 1, 65535, True), ("load8_u", 1, 65535 + 1, False),
                      ("load16_u", 2, 65534, True), ("load16_u", 2, 65535, False),
                      ("load", 4, 65532, True), ("load", 4, 65533, False),
                      ("load64-wrap", 8, 65528, True), ("load64-wrap", 8, 65529, False)][v % 8]
    _op = {"load8_u": "i32.load8_u", "load16_u": "i32.load16_u", "load64-wrap": "i64.load", "load": "i32.load"}[op]
    if not ok:
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               f'    (i32.store8 (i32.const 0) (i32.const 1))\n'
               f'    ({_op} offset={off} (i32.const 0))))') if op != "load64-wrap" else (
               '(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               f'    (i32.store8 (i32.const 0) (i32.const 1))\n'
               f'    (i32.wrap_i64 ({_op} offset={off} (i32.const 0)))))')
        return f"widths[{op},{off}]", "TRAP", wat
    if op == "load8_u":
        wat = ('(module (memory 1)\n  (func (export "f") (result i32)\n'
               f'    (i32.store8 offset={off} (i32.const 0) (i32.const 0x5A))\n'
               f'    (i32.load8_u offset={off} (i32.const 0))))')
        return f"widths[{op},{off}]", "OK 90", wat
    if op == "load16_u":
        wat = ('(module (memory 1)\n  (func (export "f") (result i32)\n'
               f'    (i32.store16 offset={off} (i32.const 0) (i32.const 0xBEEF))\n'
               f'    (i32.load16_u offset={off} (i32.const 0))))')
        return f"widths[{op},{off}]", "OK 48879", wat
    if op == "load64-wrap":
        wat = ('(module (memory 1)\n  (func (export "f") (result i32)\n'
               f'    (i64.store offset={off} (i32.const 0) (i64.const 0x1122334455667788))\n'
               f'    (i32.wrap_i64 (i64.load offset={off} (i32.const 0)))))')
        return f"widths[{op},{off}]", "OK 1432778632", wat
    wat = ('(module (memory 1)\n  (func (export "f") (result i32)\n'
           f'    (i32.store offset={off} (i32.const 0) (i32.const 4242))\n'
           f'    (i32.load offset={off} (i32.const 0))))')
    return f"widths[{op},{off}]", "OK 4242", wat


def _bigoffset(v):
    """a large-but-legal offset in a 2-page memory: reads fine mid-memory, TRAPs exactly at the size."""
    off, addr, ok = [(65536, 0, True), (131071, 0, True), (131072, 0, False),
                     (100000, 31071, True), (100000, 31072, False)][v % 5]
    if ok:
        wat = ('(module (memory 2)\n'
               '  (func (export "f") (result i32)\n'
               f'    (i32.store8 offset={off} (i32.const {addr}) (i32.const 0x77))\n'
               f'    (i32.load8_u offset={off} (i32.const {addr}))))')
        return f"bigoffset[{off}+{addr}]", "OK 119", wat
    wat = ('(module (memory 2)\n'
           '  (func (export "f") (result i32)\n'
           f'    (i32.store8 (i32.const 0) (i32.const 1))\n'
           f'    (i32.load8_u offset={off} (i32.const {addr}))))')
    return f"bigoffset[{off}+{addr}]", "TRAP", wat


_FAMILIES = [_wrapload, _wrapstore, _edge, _widths, _bigoffset]


def memarg_gen(seed):
    """Return (label, export, expected, wat): the seed-th effective-address wrap probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"memarg-{label}", "f", expected, wat


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

    print("=== memarg: the effective address offset+addr must never wrap back into the memory ===")
    bad = 0
    for s in range(33):
        label, export, expected, wat = memarg_gen(s)
        open("/tmp/ma.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/ma.wat", "-o", "/tmp/ma.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/ma.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:38} exp={expected:14} {row}")
    print(f"\n33 programs, {bad} disagreeing")
