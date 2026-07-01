"""SIMD lane-algebra generator.

This is intentionally not another GC probe. It attacks a different implementation surface: v128 lane
packing, byte order, signedness, saturating narrows, shuffle/swizzle indices, lane load/store, and SIMD
integer arithmetic. Each program returns a Python-computed checksum, so a single wrong lane is observable as
an ordinary value mismatch.
"""


class _Rng:
    def __init__(self, seed):
        self.state = (0xA5A5_1234 ^ (seed * 0x9E37_79B1)) & 0xFFFFFFFF

    def pick(self, n):
        self.state = (1103515245 * self.state + 12345) & 0xFFFFFFFF
        return self.state % n


def _u8(x):
    return x & 0xFF


def _s8(x):
    x &= 0xFF
    return x - 0x100 if x & 0x80 else x


def _u16(x):
    return x & 0xFFFF


def _s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def _i32(x):
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def _sat_s8(x):
    return max(-128, min(127, x))


def _sat_u8(x):
    return max(0, min(255, x))


def _q15mulr_sat_s(a, b):
    if a == -32768 and b == -32768:
        return 32767
    return _s16((a * b + 0x4000) >> 15)


def _v8(vals):
    return "(v128.const i8x16 " + " ".join(str(_u8(v)) for v in vals) + ")"


def _v16(vals):
    return "(v128.const i16x8 " + " ".join(str(_s16(v)) for v in vals) + ")"


def _sum(terms):
    expr = "(i32.const 0)"
    for term in terms:
        expr = f"(i32.add\n      {expr}\n      {term})"
    return expr


def _mul(term, weight):
    return f"(i32.mul {term} (i32.const {weight}))"


def _i8_extract(kind, lane, vec):
    return f"(i8x16.extract_lane_{kind} {lane} {vec})"


def _i16_extract(kind, lane, vec):
    return f"(i16x8.extract_lane_{kind} {lane} {vec})"


def _i32_extract(lane, vec):
    return f"(i32x4.extract_lane {lane} {vec})"


def _shuffle(seed):
    rng = _Rng(seed)
    a = [_u8(seed * 17 + i * 29 + 3) for i in range(16)]
    b = [_u8(seed * 31 + i * 11 + 97) for i in range(16)]
    mask = [0, 31, 15, 16] + [rng.pick(32) for _ in range(12)]
    out = (a + b)
    shuffled = [out[i] for i in mask]
    lanes = [0, 1, 2, 3, 5, 8, 13, 15]
    weights = [3, 5, 7, 11, 13, 17, 19, 23]
    vec = f"(i8x16.shuffle {' '.join(str(i) for i in mask)} {_v8(a)} {_v8(b)})"
    terms = [_mul(_i8_extract("u", lane, vec), weight) for lane, weight in zip(lanes, weights)]
    expected = sum(shuffled[lane] * weight for lane, weight in zip(lanes, weights))
    wat = f"""(module
  (func (export "f") (result i32)
    {_sum(terms)}))"""
    return "shuffle-cross-vector", f"OK {expected}", wat


def _swizzle(seed):
    rng = _Rng(seed)
    data = [_u8(7 + seed * 13 + i * 19) for i in range(16)]
    idx = [15, 0, 16, 31, 7, 128, 1, 255] + [rng.pick(24) for _ in range(8)]
    out = [data[i] if i < 16 else 0 for i in idx]
    lanes = [0, 2, 3, 5, 7, 10, 12, 15]
    weights = [2, 3, 5, 7, 11, 13, 17, 19]
    vec = f"(i8x16.swizzle {_v8(data)} {_v8(idx)})"
    terms = [_mul(_i8_extract("u", lane, vec), weight) for lane, weight in zip(lanes, weights)]
    expected = sum(out[lane] * weight for lane, weight in zip(lanes, weights))
    wat = f"""(module
  (func (export "f") (result i32)
    {_sum(terms)}))"""
    return "swizzle-oob-zero", f"OK {expected}", wat


def _narrow_s(seed):
    base = seed * 5
    lo = [-300 - base, -129, -128, -127 + base % 13, -1, 0, 1 + base % 17, 127 + base]
    hi = [128 + base, 255, -255, 32000, -32000, 42 + base, -42 - base, 126]
    out = [_sat_s8(x) for x in lo + hi]
    lanes = [0, 1, 2, 3, 7, 8, 10, 13, 15]
    weights = [3, 5, 7, 11, 13, 17, 19, 23, 29]
    vec = f"(i8x16.narrow_i16x8_s {_v16(lo)} {_v16(hi)})"
    terms = [_mul(_i8_extract("s", lane, vec), weight) for lane, weight in zip(lanes, weights)]
    expected = sum(out[lane] * weight for lane, weight in zip(lanes, weights))
    wat = f"""(module
  (func (export "f") (result i32)
    {_sum(terms)}))"""
    return "narrow-signed-saturate", f"OK {expected}", wat


def _narrow_u(seed):
    base = seed * 7
    lo = [-500 - base, -1, 0, 1, 127, 254, 255, 256 + base]
    hi = [300 + base, 511, 1024, -1024, 42 + base, 655, 128, 999]
    out = [_sat_u8(x) for x in lo + hi]
    lanes = [0, 1, 2, 5, 7, 8, 10, 11, 15]
    weights = [2, 3, 5, 7, 11, 13, 17, 19, 23]
    vec = f"(i8x16.narrow_i16x8_u {_v16(lo)} {_v16(hi)})"
    terms = [_mul(_i8_extract("u", lane, vec), weight) for lane, weight in zip(lanes, weights)]
    expected = sum(out[lane] * weight for lane, weight in zip(lanes, weights))
    wat = f"""(module
  (func (export "f") (result i32)
    {_sum(terms)}))"""
    return "narrow-unsigned-saturate", f"OK {expected}", wat


def _mul_dot(seed):
    a = [-300 + seed, 20, 123, -45, 30000, 12345, 4000 + seed, 300]
    b = [7, -8, -9, 10, 2, 3, 4, 5]
    low_s = [_i32(a[i] * b[i]) for i in range(4)]
    high_u = [_i32(_u16(a[i]) * _u16(b[i])) for i in range(4, 8)]
    dot = [_i32(a[2 * i] * b[2 * i] + a[2 * i + 1] * b[2 * i + 1]) for i in range(4)]
    v0, v1 = _v16(a), _v16(b)
    terms = [
        _mul(_i32_extract(0, f"(i32x4.extmul_low_i16x8_s {v0} {v1})"), 3),
        _mul(_i32_extract(3, f"(i32x4.extmul_low_i16x8_s {v0} {v1})"), 5),
        _mul(_i32_extract(0, f"(i32x4.extmul_high_i16x8_u {v0} {v1})"), 7),
        _mul(_i32_extract(2, f"(i32x4.extmul_high_i16x8_u {v0} {v1})"), 11),
        _mul(_i32_extract(0, f"(i32x4.dot_i16x8_s {v0} {v1})"), 13),
        _mul(_i32_extract(3, f"(i32x4.dot_i16x8_s {v0} {v1})"), 17),
    ]
    expected = _i32(low_s[0] * 3 + low_s[3] * 5 + high_u[0] * 7 + high_u[2] * 11 + dot[0] * 13 + dot[3] * 17)
    wat = f"""(module
  (func (export "f") (result i32)
    {_sum(terms)}))"""
    return "extmul-dot-signedness", f"OK {expected}", wat


def _bitselect(seed):
    a = [_u8(seed * 17 + i * 9) for i in range(16)]
    b = [_u8(255 - seed * 11 - i * 13) for i in range(16)]
    mask = [0xFF if i % 3 == 0 else 0x0F if i % 3 == 1 else 0xF0 for i in range(16)]
    selected = [_u8((a[i] & mask[i]) | (b[i] & (~mask[i] & 0xFF))) for i in range(16)]
    threshold = [_u8(40 + seed * 3 + i * 11) for i in range(16)]
    bits = sum((1 << i) for i in range(16) if selected[i] > threshold[i])
    vec = f"(v128.bitselect {_v8(a)} {_v8(b)} {_v8(mask)})"
    expr = f"(i8x16.bitmask (i8x16.gt_u {vec} {_v8(threshold)}))"
    wat = f"""(module
  (func (export "f") (result i32)
    {expr}))"""
    return "bitselect-gt-bitmask", f"OK {bits}", wat


def _memory_lane(seed):
    base = [_u8(10 + seed * 5 + i * 7) for i in range(16)]
    mem = [_u8(200 - seed * 3 + i * 17) for i in range(8)]
    loads = [(3, 0), (7, 1), (12, 2), (0, 3), (15, 4)]
    vec = list(base)
    for lane, off in loads:
        vec[lane] = mem[off]
    stores = [(24, 3), (25, 7), (26, 12), (27, 15)]
    init = "\n".join(f"    (i32.store8 (i32.const {i}) (i32.const {v}))" for i, v in enumerate(mem))
    load_ops = "\n".join(
        f"    (local.set $v (v128.load8_lane {lane} (i32.const {off}) (local.get $v)))"
        for lane, off in loads
    )
    store_ops = "\n".join(
        f"    (v128.store8_lane {lane} (i32.const {off}) (local.get $v))"
        for off, lane in stores
    )
    terms = [
        _mul("(i8x16.extract_lane_u 0 (local.get $v))", 3),
        _mul("(i8x16.extract_lane_u 3 (local.get $v))", 5),
        _mul("(i8x16.extract_lane_u 7 (local.get $v))", 7),
        _mul("(i8x16.extract_lane_u 12 (local.get $v))", 11),
        _mul("(i8x16.extract_lane_u 15 (local.get $v))", 13),
        _mul("(i32.load8_u (i32.const 24))", 17),
        _mul("(i32.load8_u (i32.const 25))", 19),
        _mul("(i32.load8_u (i32.const 26))", 23),
        _mul("(i32.load8_u (i32.const 27))", 29),
    ]
    expected = (
        vec[0] * 3 + vec[3] * 5 + vec[7] * 7 + vec[12] * 11 + vec[15] * 13 +
        vec[3] * 17 + vec[7] * 19 + vec[12] * 23 + vec[15] * 29
    )
    wat = f"""(module
  (memory 1)
  (func (export "f") (result i32) (local $v v128)
{init}
    (local.set $v {_v8(base)})
{load_ops}
{store_ops}
    {_sum(terms)}))"""
    return "memory-load-store-lanes", f"OK {expected}", wat


def _q15_avgr(seed):
    a16 = [32767, -32768, 16384, -16384, 12345 + seed, -12345, 30000, -30000]
    b16 = [32767, 32767, 16384, 16384, -12345, -12345, -30000, -30000]
    q = [_q15mulr_sat_s(a, b) for a, b in zip(a16, b16)]
    a8 = [_u8(seed * 3 + x) for x in (0, 1, 2, 3, 254, 255, 100, 101, 10, 20, 30, 40, 50, 60, 70, 80)]
    b8 = [1, 1, 3, 4, 255, 255, 101, 103, 90, 80, 70, 60, 50, 40, 30, 20]
    avg = [(_u8(a8[i]) + _u8(b8[i]) + 1) // 2 for i in range(16)]
    qvec = f"(i16x8.q15mulr_sat_s {_v16(a16)} {_v16(b16)})"
    avec = f"(i8x16.avgr_u {_v8(a8)} {_v8(b8)})"
    terms = [
        _mul(_i16_extract("s", 0, qvec), 3),
        _mul(_i16_extract("s", 1, qvec), 5),
        _mul(_i16_extract("s", 4, qvec), 7),
        _mul(_i16_extract("s", 7, qvec), 11),
        _mul(_i8_extract("u", 0, avec), 13),
        _mul(_i8_extract("u", 4, avec), 17),
        _mul(_i8_extract("u", 7, avec), 19),
        _mul(_i8_extract("u", 15, avec), 23),
    ]
    expected = q[0] * 3 + q[1] * 5 + q[4] * 7 + q[7] * 11 + avg[0] * 13 + avg[4] * 17 + avg[7] * 19 + avg[15] * 23
    wat = f"""(module
  (func (export "f") (result i32)
    {_sum(terms)}))"""
    return "q15mulr-avgr-rounding", f"OK {expected}", wat


_FAMILIES = [_shuffle, _swizzle, _narrow_s, _narrow_u, _mul_dot, _bitselect, _memory_lane, _q15_avgr]


def simdlane_gen(seed):
    """Return (label, export, expected, wat): the seed-th SIMD lane-algebra probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"simdlane-{label}", "f", expected, wat


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in ("trap", "unreachable", "runtimeerror")):
            return "TRAP"
        nums = re.findall(r"-?\d+", p.stdout or "")
        return "OK " + nums[-1] if nums else "OK _"

    print("=== simdlane: SIMD lane order, signedness, saturation, memory lanes and masks ===")
    bad = 0
    for s in range(3 * len(_FAMILIES)):
        label, export, expected, wat = simdlane_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/sl.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:120]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "--invoke", export, "/tmp/sl.wasm"],
                                capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:32} exp={expected:10} wasmtime={got}")
        else:
            print(f"  OK  {label:32} assembles")
    print(f"\n{3 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
