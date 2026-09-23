"""Random scalar integer-algebra modules for backend differentials (Cranelift O2 vs O0 vs Winch vs Pulley).

Aimed at Cranelift's mid-end rewrite rules and ISA lowering peepholes rather than at arithmetic in general:
expression trees are built from rule-shaped templates — `x % (1 << k)`, a signed range check
`x >= 0 && x < n` (with negative / zero / extreme `n`), `(x - y) == x`, `(x ^ y) == 0`, `(x - y) + (y - x)`,
comparisons of select-built `min` / `max`, `select c ~x ~y`, `x & ~x`, `x % -1`, `(x << a) >> b`
bitfield pairs with every shift amount near the width (aarch64 `ubfm` / `sbfm`), compares of zero-extended
narrow values, constant division — whose operands come from a small pool of locals, so the same wasm local
is the same SSA value exactly where a rule needs `x` twice. Leaves are loaded from a data segment of
boundary values, so nothing folds away before the rules run, while constant operands still reach the
const-matching rules. Every export folds its results into an i64 hash. No op can trap (divisors are
nonzero constants or powers of two; `div_s` never sees -1).
"""

import random
import struct

W = {"i32": 32, "i64": 64}
BOUND = {"i32": [0, 1, 2, 3, 7, 8, 31, 32, 33, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, 0x7FFFFFFF, 0x80000000,
                 0xFFFFFFFF, 0xFFFFFFFE, 0x80000001],
         "i64": [0, 1, 2, 63, 64, 65, 0xFF, 0xFFFFFFFF, 0x100000000, 0x7FFFFFFF, 0x80000000, (1 << 63) - 1,
                 1 << 63, (1 << 64) - 1, (1 << 64) - 2, (1 << 63) + 1]}
DATA_BYTES = 512


def _sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class _Gen:
    def __init__(self, r):
        self.r = r
        self.pool = {"i32": [f"$a{i}" for i in range(4)], "i64": [f"$b{i}" for i in range(4)]}

    def const(self, t, v=None):
        r = self.r
        if v is None:
            v = r.choice(BOUND[t]) if r.random() < 0.7 else r.getrandbits(W[t])
        return f"({t}.const {_sx(v, W[t])})"

    def var(self, t):
        return f"(local.get {self.r.choice(self.pool[t])})"

    def x(self, t, d):
        """An operand: mostly a pool local (so repeats hit `x op x` rules), else a constant or a subtree."""
        c = self.r.random()
        if d <= 0 or c < 0.5:
            return self.var(t)
        if c < 0.62:
            return self.const(t)
        return self.expr(t, d - 1)

    # ------------------------------------------------------------ templates
    def expr(self, t, d):
        r = self.r
        k = r.randrange(16)
        X = lambda: self.x(t, d)
        w = W[t]
        if k == 0:  # x % (1 << s)  -> x & ((1 << s) - 1)
            s = self.const(t, r.choice([0, 1, 5, w - 1, w, w + 1])) if r.random() < 0.5 else X()
            return f"({t}.rem_u {X()} ({t}.shl {self.const(t, 1)} {s}))"
        if k == 1:  # signed range check -> unsigned compare
            x = self.var(t)
            n = self.const(t, r.choice([0, 1, 5, 100, (1 << (w - 1)) - 1, 1 << (w - 1), (1 << w) - 1,
                                        r.getrandbits(w)]))
            a, b = f"({t}.ge_s {x} {self.const(t, 0)})", f"({t}.lt_s {x} {n})"
            if r.random() < 0.3:  # the le / gt flavours
                a, b = f"({t}.gt_s {x} {self.const(t, (1 << w) - 1)})", f"({t}.le_s {x} {n})"
            return self.b2t(t, f"(i32.and {a} {b})" if r.random() < 0.5 else f"(i32.and {b} {a})")
        if k == 2:  # x == ((x + (y >> s)) & m) and friends
            x, y = self.var(t), X()
            s = self.const(t, r.choice([1, w - 1, w // 2, w]))
            m = self.const(t, r.choice([(1 << w) - 1, 0xFF, (1 << (w - 1)) - 1]))
            inner = f"({t}.add {x} ({t}.shr_u {y} {s}))" if r.random() < 0.5 else f"({t}.add ({t}.shr_u {y} {s}) {x})"
            e = f"({t}.and {inner} {m})"
            return self.b2t(t, f"({t}.{r.choice(['eq', 'ne'])} {x} {e})" if r.random() < 0.5
                            else f"({t}.{r.choice(['eq', 'ne'])} {e} {x})")
        if k == 3:  # trivial remainders
            return r.choice([f"({t}.rem_u {X()} {self.const(t, 1)})", f"({t}.rem_s {X()} {self.const(t, 1)})",
                             f"({t}.rem_s {X()} {self.const(t, (1 << w) - 1)})"])
        if k == 4:  # x - x, (x - y) + (y - x)
            x, y = self.var(t), self.var(t)
            return r.choice([f"({t}.sub {x} {x})", f"({t}.add ({t}.sub {x} {y}) ({t}.sub {y} {x}))",
                             f"({t}.sub ({t}.sub {x} {y}) ({t}.sub {self.var(t)} {y}))"])
        if k == 5:  # eq / ne of (x op y) against x, y or 0
            x, y = self.var(t), self.var(t)
            op = r.choice(["sub", "add", "xor"])
            lhs = f"({t}.{op} {x} {y})" if r.random() < 0.5 else f"({t}.{op} {y} {x})"
            rhs = r.choice([x, y, self.const(t, 0)])
            cmp = r.choice(["eq", "ne"])
            return self.b2t(t, f"({t}.{cmp} {lhs} {rhs})" if r.random() < 0.5 else f"({t}.{cmp} {rhs} {lhs})")
        if k == 6:  # compares of select-built min / max
            x, y = self.var(t), self.var(t)
            sg = r.choice(["s", "u"])
            mn = f"(select {x} {y} ({t}.lt_{sg} {x} {y}))"
            mx = f"(select {x} {y} ({t}.gt_{sg} {x} {y}))" if r.random() < 0.5 else f"(select {y} {x} ({t}.lt_{sg} {x} {y}))"
            cmp = r.choice([f"gt_{sg}", f"le_{sg}", "eq", "ne", f"lt_{sg}", f"ge_{sg}"])
            return self.b2t(t, f"({t}.{cmp} {mn} {mx})" if r.random() < 0.7 else f"({t}.{cmp} {mx} {mn})")
        if k == 7:  # select c ~x ~y
            m1 = self.const(t, (1 << w) - 1)
            return (f"(select ({t}.xor {X()} {m1}) ({t}.xor {m1} {X()}) "
                    f"{self.cond(d)})")
        if k == 8:  # idempotent / annihilating bit ops
            x = self.var(t)
            m1 = self.const(t, (1 << w) - 1)
            z = self.const(t, 0)
            return r.choice([f"({t}.or {x} {x})", f"({t}.xor {x} {x})", f"({t}.and {x} ({t}.xor {x} {m1}))",
                             f"({t}.and ({t}.xor {m1} {x}) {x})", f"({t}.or {x} ({t}.xor {x} {m1}))",
                             f"({t}.xor {x} {z})", f"({t}.or {z} {x})", f"({t}.and {x} {z})",
                             f"({t}.xor ({t}.and {x} {self.var(t)}) ({t}.xor {x} {self.var(t)}))"])
        if k == 9:  # bitfield: (x << a) >> b
            a = r.choice([0, 1, 7, 8, w // 2, w - 8, w - 2, w - 1, w, w + 1, 2 * w - 1, r.randrange(w)])
            b = r.choice([0, 1, 7, 8, w // 2, w - 8, w - 2, w - 1, w, w + 1, 2 * w - 1, r.randrange(w)])
            sh = r.choice(["shr_u", "shr_s"])
            return f"({t}.{sh} ({t}.shl {X()} {self.const(t, a)}) {self.const(t, b)})"
        if k == 10:  # narrow zero-extended compares
            if t == "i64":
                cmp = r.choice(["lt_u", "le_u", "gt_u", "ge_u", "eq", "ne", "lt_s"])
                ext = r.choice(["extend_i32_u", "extend_i32_u", "extend_i32_s"])
                return self.b2t(t, f"(i64.{cmp} (i64.{ext} {self.x('i32', d)}) (i64.{ext} {self.x('i32', d)}))")
            n = r.choice([("and", 0xFF), ("and", 0xFFFF)])
            return self.b2t(t, f"(i32.{r.choice(['lt_u', 'ge_u', 'lt_s'])} (i32.and {X()} {self.const(t, n[1])}) "
                               f"(i32.and {X()} {self.const(t, n[1])}))")
        if k == 11:  # constant division (magic numbers); div_s never by -1
            dv = r.choice([2, 3, 5, 7, 10, 16, 641, (1 << (w - 1)) - 1, 1 << (w - 1), (1 << w) - 2, (1 << w) - 3])
            op = r.choice(["div_u", "div_s", "rem_u", "rem_s"])
            return f"({t}.{op} {X()} {self.const(t, dv)})"
        if k == 12:  # widen / narrow round trips
            if t == "i64":
                return r.choice([f"(i64.extend_i32_s (i32.wrap_i64 {X()}))", f"(i64.extend_i32_u (i32.wrap_i64 {X()}))",
                                 f"(i64.extend8_s {X()})", f"(i64.extend16_s {X()})", f"(i64.extend32_s {X()})",
                                 f"(i64.and (i64.extend_i32_s {self.x('i32', d)}) {self.const('i64', 0xFFFFFFFF)})"])
            return r.choice([f"(i32.wrap_i64 ({r.choice(['i64.extend_i32_s', 'i64.extend_i32_u'])} {X()}))",
                             f"(i32.extend8_s {X()})", f"(i32.extend16_s {X()})",
                             f"(i32.wrap_i64 (i64.shr_u {self.x('i64', d)} {self.const('i64', r.choice([0, 1, 31, 32, 33, 63]))}))"])
        if k == 13:  # bit counting
            return f"({t}.{r.choice(['clz', 'ctz', 'popcnt'])} {X()})"
        if k == 14:
            return f"(select {X()} {X()} {self.cond(d)})"
        op = r.choice(["add", "sub", "mul", "and", "or", "xor", "shl", "shr_s", "shr_u", "rotl", "rotr"])
        return f"({t}.{op} {X()} {X()})"

    def b2t(self, t, e):
        """An i32 boolean as a value of type t."""
        return e if t == "i32" else f"(i64.extend_i32_u {e})"

    def cond(self, d):
        r = self.r
        t = r.choice(["i32", "i64"])
        if r.random() < 0.5:
            e = self.expr(t, max(d - 1, 0))
            return e if t == "i32" else f"(i32.wrap_i64 {e})"
        return f"({t}.{r.choice(['eq', 'ne', 'lt_s', 'lt_u', 'gt_s', 'ge_u'])} {self.x(t, d - 1)} {self.x(t, d - 1)})"


def gen_module(seed, n_exports=None):
    r = random.Random(seed ^ 0x1A76)
    data = bytearray(r.getrandbits(8) for _ in range(DATA_BYTES))
    for o in range(0, DATA_BYTES, 8):  # mostly boundary values, aligned 8-byte slots
        if r.random() < 0.7:
            data[o:o + 8] = struct.pack("<Q", r.choice(BOUND["i64"] + [v | (v << 32) for v in BOUND["i32"][:12]]))
    esc = "".join(f"\\{b:02x}" for b in data)
    lines = ["(module", "  (memory 1)", f"  (data (i32.const 0) \"{esc}\")"]
    for e in range(n_exports or r.randrange(6, 16)):
        g = _Gen(r)
        body = []
        for t, names in g.pool.items():
            for n in names:
                load = r.choice(["load", "load", "load8_s", "load16_u"] + (["load32_s"] if t == "i64" else []))
                body.append(f"(local.set {n} ({t}.{load} (i32.const {r.randrange(0, DATA_BYTES - 8)})))")
        for _ in range(r.randrange(4, 14)):
            t = r.choice(["i32", "i64"])
            ex = g.expr(t, r.randrange(1, 4))
            if r.random() < 0.25:
                body.append(f"(local.set {r.choice(g.pool[t])} {ex})")
            else:
                v = ex if t == "i64" else f"(i64.extend_i32_u {ex})"
                body.append(f"(local.set $h (i64.add (i64.mul (local.get $h) (i64.const 0x100000001b3)) {v}))")
        for t, names in g.pool.items():  # the pool's final state is observable too
            for n in names:
                v = f"(local.get {n})" if t == "i64" else f"(i64.extend_i32_u (local.get {n}))"
                body.append(f"(local.set $h (i64.xor (i64.rotl (local.get $h) (i64.const 7)) {v}))")
        decl = " ".join(f"(local {n} {t})" for t, names in g.pool.items() for n in names)
        lines.append(f"  (func (export \"e{e}\") (result i64) (local $h i64) {decl}\n    "
                     + "\n    ".join(body) + "\n    (local.get $h))")
    return "\n".join(lines) + ")\n"
