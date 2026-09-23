"""Random typed SIMD expression-tree modules for backend differentials (Cranelift vs Winch vs Pulley).

Each module holds many zero-param exports; each export evaluates a random expression tree over the full
fixed-width SIMD instruction set (plus scalar glue) whose leaves come from a data segment of random bytes,
so nothing is foldable at compile time. Trees are deep and reuse subtrees through locals, which forces
spills / register pressure in a baseline compiler.

NaN handling: the spec leaves NaN payload bits (and the sign of a produced NaN) nondeterministic, so any
float-shaped value is NaN-squashed (every NaN lane replaced by one canonical pattern) before it is
reinterpreted as integers, extracted, or returned. Bit-preserving float ops (abs/neg/copysign-like) are
still exercised on raw NaNs in the middle of a tree. With that, every engine must agree bit-for-bit.
"""

import random

INT = ["i8x16", "i16x8", "i32x4", "i64x2"]
FLT = ["f32x4", "f64x2"]
LANES = {"i8x16": 16, "i16x8": 8, "i32x4": 4, "i64x2": 2, "f32x4": 4, "f64x2": 2}
SCALAR_OF = {"i8x16": "i32", "i16x8": "i32", "i32x4": "i32", "i64x2": "i64", "f32x4": "f32", "f64x2": "f64"}
MEM_BYTES = 4096


def _is_float(shape):
    return shape in FLT or shape in ("f32", "f64")


class Gen:
    def __init__(self, rng, max_depth=6, tee_p=0.15):
        self.r = rng
        self.max_depth = max_depth
        self.tee_p = tee_p
        self.locals = []  # (name, kind) kind: 'v128' or scalar
        self.pending = []  # local.tee reuse candidates: (name, shape)

    # ---- leaves ----------------------------------------------------------
    def addr(self, size):
        return self.r.randrange(0, MEM_BYTES - size - 64)

    def leaf_v(self, shape):
        r = self.r
        c = r.random()
        if self.pending and c < 0.25:
            name, sh = r.choice(self.pending)
            if sh in FLT and sh != shape:
                return self.squash(f"(local.get ${name})", sh), sh
            return f"(local.get ${name})", sh
        if c < 0.55:
            a = self.addr(16)
            off = r.choice([0, 0, 1, 3, 16, 17])
            return f"(v128.load offset={off} align={r.choice([1, 2, 4, 8, 16])} (i32.const {a}))", shape
        if c < 0.70:
            op = r.choice(["v128.load8x8_s", "v128.load8x8_u", "v128.load16x4_s", "v128.load16x4_u",
                           "v128.load32x2_s", "v128.load32x2_u"])
            sh = {"8": "i16x8", "16": "i32x4", "32": "i64x2"}[op.split("load")[1].split("x")[0]]
            return f"({op} (i32.const {self.addr(8)}))", sh
        if c < 0.80:
            op, sh = r.choice([("v128.load8_splat", "i8x16"), ("v128.load16_splat", "i16x8"),
                               ("v128.load32_splat", "i32x4"), ("v128.load64_splat", "i64x2"),
                               ("v128.load32_zero", "i32x4"), ("v128.load64_zero", "i64x2")])
            return f"({op} (i32.const {self.addr(8)}))", sh
        if c < 0.88:
            # special constants
            pats = [0, (1 << 128) - 1, 0x80000000_80000000_80000000_80000000, 0x7fc00000_ff800000_7f800000_80000000,
                    0x7ff8000000000000_fff0000000000000, 0x8080808080808080_7f7f7f7f7f7f7f7f,
                    0x00010203040506070809_0a0b0c0d0e0f, 0x0000_ffff_8000_7fff_0001_fffe_8001_7ffe]
            v = r.choice(pats + [r.getrandbits(128)])
            return f"(v128.const i64x2 {v & (2**64 - 1)} {v >> 64})", shape
        return self.splat(shape), shape

    def splat(self, shape):
        sc = SCALAR_OF[shape]
        return f"({shape}.splat {self.scalar(sc, 2)})"

    def scalar(self, t, depth):
        r = self.r
        c = r.random()
        if depth > 0 and c < 0.35:
            # extract from a vector
            sh = r.choice([s for s in INT + FLT if SCALAR_OF[s] == t] or ["i32x4"])
            if SCALAR_OF[sh] != t:
                sh = "i32x4" if t == "i32" else "i64x2"
            v = self.vexpr(sh, depth - 1)
            lane = r.randrange(LANES[sh])
            sfx = r.choice(["_s", "_u"]) if sh in ("i8x16", "i16x8") else ""
            e = f"({sh}.extract_lane{sfx} {lane} {self.squash(v, sh)})"
            return self.squash_scalar(e, t)
        if t == "i32" and depth > 0 and c < 0.5:
            sh = r.choice(INT + FLT)
            v = self.squash(self.vexpr(sh, depth - 1), sh)
            op = r.choice(["v128.any_true", f"{self._ish(sh)}.all_true", f"{self._ish(sh)}.bitmask"])
            return f"({op} {v})"
        if c < 0.75:
            load = {"i32": ["i32.load", "i32.load8_s", "i32.load16_u"], "i64": ["i64.load", "i64.load32_s"],
                    "f32": ["f32.load"], "f64": ["f64.load"]}[t]
            return self.squash_scalar(f"({r.choice(load)} (i32.const {self.addr(8)}))", t)
        vals = {"i32": [0, 1, -1, 7, 8, 15, 16, 31, 32, 33, 63, 64, 127, 128, 255, -128, 0x7fffffff, -0x80000000],
                "i64": [0, 1, -1, 63, 64, 0x7fffffffffffffff, -0x8000000000000000, 0xffffffff],
                "f32": ["0", "-0", "1", "-1.5", "inf", "-inf", "0x1p-149", "3.4e38", "2147483648", "-2147483904",
                        "4294967296"],
                "f64": ["0", "-0", "1", "-1.5", "inf", "-inf", "0x1p-1074", "1.7e308", "2147483647.5",
                        "-2147483648.9", "4294967295.5", "9.3e18"]}[t]
        return f"({t}.const {r.choice(vals)})"

    def _ish(self, sh):
        return {"f32x4": "i32x4", "f64x2": "i64x2"}.get(sh, sh)

    def squash(self, e, shape):
        if shape not in FLT:
            return e
        canon = "0x7fc00000" if shape == "f32x4" else "0x7ff8000000000000"
        lanes = " ".join([canon] * LANES[shape])
        ish = self._ish(shape)
        name = self.new_local("v128")
        return (f"(v128.bitselect (v128.const {ish} {lanes}) (local.tee ${name} {e}) "
                f"({shape}.ne (local.get ${name}) (local.get ${name})))")

    def squash_scalar(self, e, t):
        if t not in ("f32", "f64"):
            return e
        name = self.new_local(t)
        return (f"(select ({t}.const nan) (local.tee ${name} {e}) "
                f"({t}.ne (local.get ${name}) (local.get ${name})))")

    def new_local(self, kind):
        name = f"l{len(self.locals)}"
        self.locals.append((name, kind))
        return name

    # ---- vector expressions ---------------------------------------------
    def vexpr(self, shape, depth):
        r = self.r
        if depth <= 0 or r.random() < 0.12:
            e, sh = self.leaf_v(shape)
            return e  # bit-level reinterpretation between shapes is free (v128 is untyped)
        e = self._vexpr(shape, depth)
        if r.random() < self.tee_p:
            name = self.new_local("v128")
            self.pending.append((name, shape))
            return f"(local.tee ${name} {e})"
        return e

    def arg(self, shape, depth):
        """An operand of `shape`: sub-expressions of a different float shape are squashed first."""
        r = self.r
        src = shape if r.random() < 0.7 else r.choice(INT + FLT)
        e = self.vexpr(src, depth - 1)
        if src in FLT and src != shape:
            return self.squash(e, src)
        return e

    def _vexpr(self, shape, d):
        r = self.r
        A = lambda: self.arg(shape, d)
        if r.random() < 0.12:
            k = r.choice(["not", "and", "andnot", "or", "xor", "bitselect"])
            if shape in FLT:  # bitwise ops on floats are payload-visible; squash inputs
                A = lambda: self.squash(self.arg(shape, d), shape)
            if k == "not":
                return f"(v128.not {A()})"
            if k == "bitselect":
                return f"(v128.bitselect {A()} {A()} {self.arg('i8x16', d)})"
            return f"(v128.{k} {A()} {A()})"
        if shape in FLT:  # byte-granular / bitwise movement exposes NaN payloads: squash inputs
            B = lambda: self.squash(self.arg(shape, d), shape)
        else:
            B = A
        if r.random() < 0.08:
            lanes = " ".join(str(r.randrange(32)) for _ in range(16))
            return f"(i8x16.shuffle {lanes} {B()} {B()})"
        if r.random() < 0.05:
            return f"(i8x16.swizzle {B()} {self.arg('i8x16', d)})"
        if r.random() < 0.08:
            lane = r.randrange(LANES[shape])
            return f"({shape}.replace_lane {lane} {A()} {self.scalar(SCALAR_OF[shape], d - 1)})"
        if shape in INT:
            return self._int(shape, d, A)
        return self._flt(shape, d, A)

    def _int(self, sh, d, A):
        r = self.r
        ops = ["add", "sub", "eq", "ne", "lt_s", "gt_s", "le_s", "ge_s", "abs", "neg", "shl", "shr_s", "shr_u",
               "conv"]
        if sh != "i64x2":
            ops += ["lt_u", "gt_u", "le_u", "ge_u", "min_s", "min_u", "max_s", "max_u"]
        if sh != "i8x16":
            ops += ["mul", "extmul", "extend"]
        if sh in ("i8x16", "i16x8"):
            ops += ["add_sat_s", "add_sat_u", "sub_sat_s", "sub_sat_u", "avgr_u", "narrow"]
        if sh == "i8x16":
            ops += ["popcnt"]
        if sh in ("i16x8", "i32x4"):
            ops += ["extadd"]
        if sh == "i16x8":
            ops += ["q15mulr_sat_s"]
        if sh == "i32x4":
            ops += ["dot", "trunc_sat"]
        op = r.choice(ops)
        if op in ("abs", "neg", "popcnt"):
            return f"({sh}.{op} {A()})"
        if op in ("shl", "shr_s", "shr_u"):
            return f"({sh}.{op} {A()} {self.scalar('i32', d - 1)})"
        half = {"i16x8": "i8x16", "i32x4": "i16x8", "i64x2": "i32x4"}.get(sh)
        if op == "extmul":
            return f"({sh}.extmul_{r.choice(['low', 'high'])}_{half}_{r.choice('su')} {self.arg(half, d)} {self.arg(half, d)})"
        if op == "extend":
            return f"({sh}.extend_{r.choice(['low', 'high'])}_{half}_{r.choice('su')} {self.arg(half, d)})"
        if op == "extadd":
            return f"({sh}.extadd_pairwise_{half}_{r.choice('su')} {self.arg(half, d)})"
        if op == "narrow":
            wide = {"i8x16": "i16x8", "i16x8": "i32x4"}[sh]
            return f"({sh}.narrow_{wide}_{r.choice('su')} {self.arg(wide, d)} {self.arg(wide, d)})"
        if op == "dot":
            return f"(i32x4.dot_i16x8_s {self.arg('i16x8', d)} {self.arg('i16x8', d)})"
        if op == "trunc_sat":
            if r.random() < 0.5:
                return f"(i32x4.trunc_sat_f32x4_{r.choice('su')} {self.arg('f32x4', d)})"
            return f"(i32x4.trunc_sat_f64x2_{r.choice('su')}_zero {self.arg('f64x2', d)})"
        if op == "conv":
            return self.vexpr(sh, d - 1)
        return f"({sh}.{op} {A()} {A()})"

    def _flt(self, sh, d, A):
        r = self.r
        ops = ["add", "sub", "mul", "div", "min", "max", "pmin", "pmax", "eq", "ne", "lt", "gt", "le", "ge",
               "abs", "neg", "sqrt", "ceil", "floor", "trunc", "nearest", "conv"]
        op = r.choice(ops)
        if op in ("abs", "neg", "sqrt", "ceil", "floor", "trunc", "nearest"):
            return f"({sh}.{op} {A()})"
        if op in ("eq", "ne", "lt", "gt", "le", "ge"):
            return f"({sh}.{op} {A()} {A()})"  # result is an int mask: never NaN
        if op == "conv":
            if sh == "f32x4":
                c = r.choice(["cs", "cu", "demote"])
                if c == "demote":
                    return f"(f32x4.demote_f64x2_zero {self.arg('f64x2', d)})"
                return f"(f32x4.convert_i32x4_{c[1]} {self.arg('i32x4', d)})"
            c = r.choice(["cs", "cu", "promote"])
            if c == "promote":
                return f"(f64x2.promote_low_f32x4 {self.arg('f32x4', d)})"
            return f"(f64x2.convert_low_i32x4_{c[1]} {self.arg('i32x4', d)})"
        return f"({sh}.{op} {A()} {A()})"


def _data(r):
    import struct
    data = bytearray(r.getrandbits(8) for _ in range(MEM_BYTES))
    # sprinkle interesting float / int patterns into the data
    specials = [struct.pack("<f", x) for x in (0.0, -0.0, float("inf"), float("-inf"), 1.5, -2.5, 2147483648.0,
                                               4294967296.0, -2147483904.0, 1e-45)]
    specials += [struct.pack("<d", x) for x in (0.0, -0.0, float("inf"), 1e308, 2147483647.5, -2147483649.0,
                                                4294967296.0, 5e-324)]
    specials += [struct.pack("<I", x) for x in (0x7fc00001, 0xffc00000, 0x7f800001, 0x80000000, 0x7fffffff)]
    for _ in range(300):
        s = r.choice(specials)
        o = r.randrange(0, MEM_BYTES - 8)
        data[o:o + len(s)] = s
    return "".join(f"\\{b:02x}" for b in data)


_ROT1 = " ".join(str((i + 1) % 16) for i in range(16))


def gen_module_obs(seed, n_funcs=16, max_depth=5):
    """Like gen_module, but every export folds each teed intermediate into its result (byte-rotate + xor),
    so a wrong value deep in a tree stays observable even when the root saturates to a constant mask."""
    r = random.Random(seed ^ 0x0B5E7)
    funcs = []
    for fi in range(n_funcs):
        g = Gen(r, max_depth, tee_p=0.4)
        sh = r.choice(INT + FLT)
        body = [f"(local.set $acc {g.squash(g.vexpr(sh, r.randrange(2, max_depth + 1)), sh)})"]
        for name, psh in g.pending:
            body.append(f"(local.set $acc (v128.xor (i8x16.shuffle {_ROT1} (local.get $acc) (local.get $acc)) "
                        f"{g.squash(f'(local.get ${name})', psh)}))")
        locs = " ".join(f"(local ${n} {k})" for n, k in g.locals)
        funcs.append(f"  (func (export \"f{fi}\") (result v128) (local $acc v128) {locs}\n    "
                     + "\n    ".join(body) + "\n    (local.get $acc))")
    return "(module\n  (memory 1)\n  (data (i32.const 0) \"" + _data(r) + "\")\n" + "\n".join(funcs) + ")\n"


def gen_module(seed, n_funcs=24, max_depth=6):
    r = random.Random(seed)
    funcs = []
    for fi in range(n_funcs):
        g = Gen(r, max_depth)
        kind = r.random()
        if kind < 0.8:
            sh = r.choice(INT + FLT)
            body = g.squash(g.vexpr(sh, r.randrange(2, max_depth + 1)), sh)
            res = "v128"
        else:
            t = r.choice(["i32", "i64", "f32", "f64"])
            body = g.scalar(t, r.randrange(2, max_depth + 1))
            body = g.squash_scalar(body, t) if t in ("f32", "f64") else body
            res = t
        locs = " ".join(f"(local ${n} {k})" for n, k in g.locals)
        funcs.append(f"  (func (export \"f{fi}\") (result {res}) {locs}\n    {body})")
    data = bytes(r.getrandbits(8) for _ in range(MEM_BYTES))
    # sprinkle interesting float / int patterns into the data
    import struct
    specials = [struct.pack("<f", x) for x in (0.0, -0.0, float("inf"), float("-inf"), 1.5, -2.5, 2147483648.0,
                                               4294967296.0, -2147483904.0, 1e-45)]
    specials += [struct.pack("<d", x) for x in (0.0, -0.0, float("inf"), 1e308, 2147483647.5, -2147483649.0,
                                                4294967296.0, 5e-324)]
    specials += [struct.pack("<I", x) for x in (0x7fc00001, 0xffc00000, 0x7f800001, 0x80000000, 0x7fffffff)]
    data = bytearray(data)
    for _ in range(300):
        s = r.choice(specials)
        o = r.randrange(0, MEM_BYTES - 8)
        data[o:o + len(s)] = s
    esc = "".join(f"\\{b:02x}" for b in data)
    return "(module\n  (memory 1)\n  (data (i32.const 0) \"" + esc + "\")\n" + "\n".join(funcs) + ")\n"
