"""Bulk-op extent wrap: `dst + len`, `src + len` — and grow's `current + count` — are computed EXACTLY, never in a u32.

The same disease vector that won twice (memory64 #10 truncated the ADDRESS; wasmz#12 folded the memarg
offset+addr sum): here the operands are the bulk-operation extents. `memory.fill (dst=0xFFFFFF00,
len=0x200)` has dst+len = 0x100000100 — out of bounds, so the op must TRAP; an engine forming the sum in
u32 wraps it to 0x100 <= 65536, passes its own check, and executes a 512-byte fill at a wild address — a
heap overflow, not a wrong value. Every family pairs wrap shapes with in-bounds controls and plain-OOB
controls, so a divergence isolates the wrap: fill / copy (both the source and destination sums; a wrapped
copy that truncates the source visibly returns the sentinel planted at 0) / memory.init against a passive
segment (segment-extent wrap, the exact segment end, and the dropped-segment zero-length case Wizard
over-trapped) / table.fill / copy / init on funcrefs / memory.grow argument wrap (`grow(0xFFFFFFFF)`
must FAIL with -1, not wrap current+count into success — and a failed grow preserves both content and
bounds). Single memory throughout — no multi-memory flag — so every engine runs every case; results i32.
"""

_HEX = "0x%X"


def _fillwrap(v):
    """memory.fill extent wrap: dst+len crossing 2^32 must TRAP, never fill at the wild dst."""
    dst, val, ln, exp = [(0xFFFFFF00, 66, 0x200, "TRAP"), (0x10000, 66, 1, "TRAP"),
                         (0, 0x5A, 4, f"OK {0x5A5A5A5A}"), (65532, 0x77, 4, "OK 119"),
                         (0x100, 66, 0xFFFFFF00, "TRAP")][v % 5]
    if exp == "TRAP":
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (i32.store (i32.const 0) (i32.const 4242))\n'
               f'    (memory.fill (i32.const {dst if dst < 0x80000000 else _HEX % dst}) (i32.const {val})'
               f' (i32.const {ln if ln < 0x80000000 else _HEX % ln}))\n'
               '    (i32.const 0)))')
        return f"fill[dst={_HEX % dst},len={_HEX % ln}]", exp, wat
    if dst == 0:
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               f'    (memory.fill (i32.const 0) (i32.const {val}) (i32.const {ln}))\n'
               '    (i32.load (i32.const 0))))')
        return f"fill[dst=0,len={ln}]", exp, wat
    wat = ('(module (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           f'    (memory.fill (i32.const {dst}) (i32.const {val}) (i32.const {ln}))\n'
           f'    (i32.load8_u (i32.const 65535))))')
    return f"fill[last-word]", exp, wat


def _copywrap(v):
    """memory.copy extent wrap: src+len crossing 2^32 must TRAP — a truncating engine copies the sentinel."""
    d, s, ln, exp = [(8, 0xFFFFFFFC, 4, "TRAP"), (0xFFFFFFFC, 0, 4, "TRAP"), (0xFFFFFFF8, 0xFFFFFFF8, 8, "TRAP"),
                     (4, 0, 4, "OK 287454020")][v % 4]
    wat = ('(module (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           f'    (i32.store (i32.const 0) (i32.const 287454020))\n'          # 0x11223344 sentinel
           f'    (memory.copy (i32.const {d if d < 0x80000000 else _HEX % d}) (i32.const {s if s < 0x80000000 else _HEX % s})'
           f' (i32.const {ln if ln < 0x80000000 else _HEX % ln}))\n'
           f'    (i32.load (i32.const {8 if exp != "TRAP" and d == 8 else 0}))))')
    return f"copy[dst={_HEX % d},src={_HEX % s},len={ln}]", exp, wat


def _initwrap(v):
    """memory.init vs a 16-byte passive segment: extent wrap, the exact segment end, the dropped case."""
    d, s, ln, exp, drop = [(0, 0xFFFFFFFC, 4, "TRAP", False), (0xFFFFFFFC, 0, 4, "TRAP", False),
                           (0, 12, 4, "OK 1347374669", False), (0, 13, 4, "TRAP", False),
                           (0, 0, 0, "OK 4242", True), (0, 0, 1, "TRAP", True)][v % 6]
    drop_line = "    (data.drop $d)\n" if drop else ""
    store_line = "" if not drop else "    (i32.store (i32.const 0) (i32.const 4242))\n"
    wat = ('(module (memory 1) (data $d "ABCDEFGHIJKLMNOP")\n'
           '  (func (export "f") (result i32)\n'
           f'{store_line}{drop_line}'
           f'    (memory.init $d (i32.const {d if d < 0x80000000 else _HEX % d})'
           f' (i32.const {s if s < 0x80000000 else _HEX % s}) (i32.const {ln}))\n'
           f'    (i32.load (i32.const 0))))')
    return f"init[dst={_HEX % d},src={_HEX % s},len={ln},drop={int(drop)}]", exp, wat


def _tablewrap(v):
    """table.fill / copy / init extent wrap on funcrefs — same laws, the reference-table machinery."""
    if v % 5 == 0:      # fill idx+len wraps to 2 <= 4 — must TRAP
        wat = ('(module (table 4 funcref) (func $g (result i32) (i32.const 4242))\n'
               '  (func (export "f") (result i32)\n'
               '    (table.fill (i32.const 0xFFFFFFFE) (ref.func $g) (i32.const 4))\n'
               '    (i32.const 0)))')
        return "tablefill[idx=0xFFFFFFFE,len=4]", "TRAP", wat
    if v % 5 == 1:      # copy dst+len wraps
        wat = ('(module (table 4 funcref) (func $g (result i32) (i32.const 4242)) (elem (i32.const 0) $g $g)\n'
               '  (func (export "f") (result i32)\n'
               '    (table.copy (i32.const 0xFFFFFFFE) (i32.const 0) (i32.const 4))\n'
               '    (i32.const 0)))')
        return "tablecopy[dst=0xFFFFFFFE]", "TRAP", wat
    if v % 5 == 2:      # init from a passive elem segment, dst+len wraps
        wat = ('(module (table 4 funcref) (func $g (result i32) (i32.const 4242)) (elem $e func $g $g)\n'
               '  (func (export "f") (result i32)\n'
               '    (table.init $e (i32.const 0xFFFFFFFE) (i32.const 0) (i32.const 2))\n'
               '    (i32.const 0)))')
        return "tableinit[dst=0xFFFFFFFE]", "TRAP", wat
    if v % 5 == 3:      # in-bounds control: fill+copy+call_indirect lands the baked constant
        wat = ('(module (type $t (func (result i32))) (table 4 funcref)\n'
               '  (func $g (result i32) (i32.const 4242)) (elem declare func $g)\n'
               '  (func (export "f") (result i32)\n'
               '    (table.fill (i32.const 0) (ref.func $g) (i32.const 2))\n'
               '    (table.copy (i32.const 2) (i32.const 0) (i32.const 2))\n'
               '    (call_indirect (type $t) (i32.const 3))))')
        return "tablectrl[fill+copy+call3]", "OK 4242", wat
    wat = ('(module (table 4 funcref) (func $g (result i32) (i32.const 4242)) (elem $e func $g $g)\n'
           '  (func (export "f") (result i32)\n'
           '    (table.init $e (i32.const 0) (i32.const 0) (i32.const 2))\n'
           '    (ref.is_null (table.get (i32.const 1)))))')
    return "tablectrl[init+get1]", "OK 0", wat


def _growwrap(v):
    """memory.grow argument wrap: a count near 2^32 must FAIL (-1), not wrap current+count into success."""
    if v % 5 == 0:
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (if (result i32) (i32.eq (memory.grow (i32.const 0xFFFFFFFF)) (i32.const -1))\n'
               '      (then (i32.const 7)) (else (i32.const 13)))))')
        return "grow[0xFFFFFFFF-fails]", "OK 7", wat
    if v % 5 == 1:
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (if (result i32) (i32.eq (memory.grow (i32.const 65536)) (i32.const -1))\n'
               '      (then (i32.const 7)) (else (i32.const 13)))))')
        return "grow[65536-fails]", "OK 7", wat
    if v % 5 == 2:
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (drop (memory.grow (i32.const 0)))\n'
               '    (i32.const 4242)))')
        return "grow[0-noop]", "OK 4242", wat
    if v % 5 == 3:      # a failed grow preserves content
        wat = ('(module (memory 1)\n'
               '  (func (export "f") (result i32)\n'
               '    (i32.store (i32.const 0) (i32.const 777))\n'
               '    (drop (memory.grow (i32.const 0xFFFFFFFF)))\n'
               '    (i32.load (i32.const 0))))')
        return "grow[fail-preserves]", "OK 777", wat
    wat = ('(module (memory 1)\n'
           '  (func (export "f") (result i32)\n'
           '    (drop (memory.grow (i32.const 0xFFFFFFFF)))\n'
           '    (i32.load (i32.const 65536))))')
    return "grow[fail-keeps-bounds]", "TRAP", wat


_FAMILIES = [_fillwrap, _copywrap, _initwrap, _tablewrap, _growwrap]


def bulkwrap_gen(seed):
    """Return (label, export, expected, wat): the seed-th bulk-extent wrap probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"bulkwrap-{label}", "f", expected, wat


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
                "execution failed", "null", "divide", "runtimeerror", "fail", "error")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True, timeout=300))

    print("=== bulkwrap: bulk extents dst+len / src+len / grow counts never wrap in 32 bits ===")
    bad = 0
    for s in range(25):
        label, export, expected, wat = bulkwrap_gen(s)
        assert wat.count("(") == wat.count(")"), f"paren imbalance in {label}"
        open("/tmp/bw.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/bw.wat", "-o", "/tmp/bw.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/bw.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:44} exp={expected:14} {row}")
    print(f"\n25 programs, {bad} disagreeing")
