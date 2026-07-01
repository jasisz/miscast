"""Multi-memory / bulk-memory cross-index generator.

These probes are outside the GC and SIMD lanes. They stress engines that lower memory instructions through
an indexed-memory path: multiple memories, cross-memory copy, passive data init, memory.fill isolation,
overlapping memmove semantics, grow on a nonzero memory index, and OOB traps on the selected memory.
"""


def _sum(terms):
    expr = "(i32.const 0)"
    for term in terms:
        expr = f"(i32.add\n      {expr}\n      {term})"
    return expr


def _mul(expr, weight):
    return f"(i32.mul {expr} (i32.const {weight}))"


def _load(mem, off):
    return f"(i32.load8_u ${mem} (i32.const {off}))"


def _data_bytes(seed, n):
    return [(seed * 17 + i * 23 + 31) & 0xFF for i in range(n)]


def _wat_string(vals):
    return "".join(f"\\{v:02x}" for v in vals)


def _independent(seed):
    a, b, c = 7 + seed, 90 + seed * 3, 170 - seed
    expected = a * 3 + b * 5 + c * 7
    wat = f"""(module
  (memory $a 1)
  (memory $b 1)
  (memory $c 1)
  (func (export "f") (result i32)
    (i32.store8 $a (i32.const 0) (i32.const {a}))
    (i32.store8 $b (i32.const 0) (i32.const {b}))
    (i32.store8 $c (i32.const 0) (i32.const {c}))
    {_sum([_mul(_load("a", 0), 3), _mul(_load("b", 0), 5), _mul(_load("c", 0), 7)])}))"""
    return "independent-memory-indexes", f"OK {expected}", wat


def _init_copy(seed):
    data = _data_bytes(seed, 12)
    dst = 20 + seed % 5
    src = 2 + seed % 3
    length = 7
    copied = data[src:src + length]
    expected = copied[0] * 3 + copied[3] * 5 + copied[6] * 7
    wat = f"""(module
  (memory $a 1)
  (memory $b 1)
  (data $d "{_wat_string(data)}")
  (func (export "f") (result i32)
    (memory.init $a $d (i32.const {dst}) (i32.const {src}) (i32.const {length}))
    (memory.copy $b $a (i32.const 100) (i32.const {dst}) (i32.const {length}))
    {_sum([_mul(_load("b", 100), 3), _mul(_load("b", 103), 5), _mul(_load("b", 106), 7)])}))"""
    return "passive-data-cross-copy", f"OK {expected}", wat


def _fill_isolation(seed):
    va, vb = 41 + seed * 2, 200 - seed
    expected = va * 11 + vb * 13 + va * 17 + vb * 19
    wat = f"""(module
  (memory $a 1)
  (memory $b 1)
  (func (export "f") (result i32)
    (memory.fill $a (i32.const 10) (i32.const {va}) (i32.const 5))
    (memory.fill $b (i32.const 10) (i32.const {vb}) (i32.const 5))
    {_sum([_mul(_load("a", 10), 11), _mul(_load("b", 10), 13),
           _mul(_load("a", 14), 17), _mul(_load("b", 14), 19)])}))"""
    return "fill-isolation", f"OK {expected}", wat


def _overlap(seed):
    vals = [(seed * 5 + i * 13 + 1) & 0xFF for i in range(10)]
    after = list(vals)
    tmp = after[0:7]
    for i, v in enumerate(tmp):
        after[2 + i] = v
    expected = after[2] * 3 + after[3] * 5 + after[7] * 7 + after[8] * 11
    stores = "\n".join(f"    (i32.store8 $a (i32.const {i}) (i32.const {v}))" for i, v in enumerate(vals))
    wat = f"""(module
  (memory $a 1)
  (memory $b 1)
  (func (export "f") (result i32)
{stores}
    (memory.copy $a $a (i32.const 2) (i32.const 0) (i32.const 7))
    (memory.copy $b $a (i32.const 30) (i32.const 2) (i32.const 7))
    {_sum([_mul(_load("b", 30), 3), _mul(_load("b", 31), 5),
           _mul(_load("b", 35), 7), _mul(_load("b", 36), 11)])}))"""
    return "overlap-then-cross-copy", f"OK {expected}", wat


def _grow_second(seed):
    off = 65536 + 19 + seed
    val = 33 + seed * 9
    expected = 2 * 5 + val * 7
    wat = f"""(module
  (memory $a 1)
  (memory $b 1)
  (func (export "f") (result i32)
    (drop (memory.grow $b (i32.const 1)))
    (i32.store8 $b (i32.const {off}) (i32.const {val}))
    {_sum([_mul("(memory.size $b)", 5), _mul(_load("b", off), 7)])}))"""
    return "grow-nonzero-memory", f"OK {expected}", wat


def _copy_from_grown(seed):
    off = 65536 + 29 + seed
    val = 120 + seed
    expected = val * 3 + 0 * 5 + val * 7
    wat = f"""(module
  (memory $a 1)
  (memory $b 1)
  (func (export "f") (result i32)
    (drop (memory.grow $b (i32.const 1)))
    (i32.store8 $b (i32.const {off}) (i32.const {val}))
    (memory.copy $a $b (i32.const 40) (i32.const {off}) (i32.const 1))
    {_sum([_mul(_load("a", 40), 3), _mul(_load("a", 41), 5), _mul(_load("b", off), 7)])}))"""
    return "copy-from-grown-memory", f"OK {expected}", wat


def _copy_oob(_seed):
    wat = """(module
  (memory $a 1)
  (memory $b 1)
  (func (export "f") (result i32)
    (memory.copy $b $a (i32.const 65534) (i32.const 0) (i32.const 3))
    (i32.const 99)))"""
    return "cross-copy-destination-oob", "TRAP", wat


def _init_oob(_seed):
    wat = """(module
  (memory $a 1)
  (memory $b 1)
  (data $d "\\01\\02\\03\\04")
  (func (export "f") (result i32)
    (memory.init $b $d (i32.const 65535) (i32.const 0) (i32.const 2))
    (i32.const 99)))"""
    return "memory-init-destination-oob", "TRAP", wat


_FAMILIES = [_independent, _init_copy, _fill_isolation, _overlap, _grow_second, _copy_from_grown,
             _copy_oob, _init_oob]


def memcross_gen(seed):
    """Return (label, export, expected, wat): the seed-th multi-memory cross-index probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"memcross-{label}", "f", expected, wat


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in ("trap", "out of bounds", "runtimeerror")):
            return "TRAP"
        nums = re.findall(r"-?\d+", p.stdout or "")
        return "OK " + nums[-1] if nums else "OK _"

    print("=== memcross: multi-memory bulk-memory index and boundary probes ===")
    bad = 0
    for s in range(3 * len(_FAMILIES)):
        label, export, expected, wat = memcross_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/mc.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:120]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "--invoke", export, "/tmp/mc.wasm"],
                                capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:34} exp={expected:8} wasmtime={got}")
        else:
            print(f"  OK  {label:34} assembles")
    print(f"\n{3 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
