"""Wide-arithmetic probes (`i64.add128`, `i64.sub128`, `i64.mul_wide_s`, `i64.mul_wide_u`).

These ops return two i64 values (low, high). Each export feeds them boundary operands (0, 1, -1, 2^63, carries and
borrows across the 64-bit boundary) and consumes the pair every way the stack allows: `local.set` of both halves
in the natural order, `local.tee`, dropping one half, feeding the pair straight into another wide op, or folding
both into the hash. Engines that fuse the op with the following `local.set` are the target. The expected results
come from wasmtime (miscast.wtref), which has wide-arithmetic enabled in wtdiff.
"""

import random

BOUND = [0, 1, 2, -1, -2, (1 << 63) - 1, -(1 << 63), 0xFFFFFFFF, 1 << 32, 0x7FFFFFFFFFFFFFFE, -(1 << 62)]


def _sx64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


class _Gen:
    def __init__(self, r):
        self.r = r

    def x(self):
        r = self.r
        if r.random() < 0.55:
            return f"(local.get $p{r.randrange(6)})"
        return f"(i64.const {_sx64(r.choice(BOUND) if r.random() < 0.8 else r.getrandbits(64))})"

    def wide(self):
        """One wide op as unfolded instructions leaving (lo, hi) on the stack."""
        r = self.r
        op = r.choice(["i64.add128", "i64.sub128", "i64.mul_wide_s", "i64.mul_wide_u"])
        n = 4 if op in ("i64.add128", "i64.sub128") else 2
        return " ".join(self.x() for _ in range(n)) + f" {op}"

    def mix(self, e):
        return f"(local.set $h (i64.add (i64.mul (local.get $h) (i64.const 0x100000001b3)) {e}))"

    def stmt(self):
        r = self.r
        k = r.randrange(6)
        a, b = f"$p{r.randrange(6)}", f"$p{r.randrange(6)}"
        if k == 0:  # hi into b, lo into a (stack order: hi is on top)
            return f"{self.wide()} (local.set {b}) (local.set {a}) {self.mix(f'(i64.xor (local.get {a}) (i64.rotl (local.get {b}) (i64.const 1)))')}"
        if k == 1:  # tee the high half, keep using it
            return f"{self.wide()} (local.tee {b}) (local.set $h (i64.add (local.get $h))) (local.set {a}) {self.mix(f'(local.get {a})')}"
        if k == 2:  # drop the high half
            return f"{self.wide()} (drop) (local.set {a}) {self.mix(f'(local.get {a})')}"
        if k == 3:  # drop the low half: keep hi through a temp
            return f"{self.wide()} (local.set $t) (drop) {self.mix('(local.get $t)')}"
        if k == 4 and True:  # chain: the (lo, hi) pair of one add128 feeds the first operand pair of another
            op = r.choice(["i64.add128", "i64.sub128"])
            first = " ".join(self.x() for _ in range(4)) + f" {op}"
            return f"{first} {self.x()} {self.x()} {op} (local.set {b}) (local.set {a}) {self.mix(f'(i64.sub (local.get {a}) (local.get {b}))')}"
        # write back into the very locals used as operands
        op = r.choice(["i64.add128", "i64.sub128"])
        return (f"(local.get {a}) (local.get {b}) (local.get {b}) (local.get {a}) {op} (local.set {a}) (local.set {b}) "
                f"{self.mix(f'(i64.add (local.get {a}) (local.get {b}))')}")


def gen_module(seed, n_exports=None):
    r = random.Random(seed ^ 0x3D17E)
    lines = ["(module"]
    for e in range(n_exports or r.randrange(4, 10)):
        g = _Gen(r)
        body = [f"(local.set $p{i} (i64.const {_sx64(r.choice(BOUND) if r.random() < 0.6 else r.getrandbits(64))}))"
                for i in range(6)]
        body += [g.stmt() for _ in range(r.randrange(3, 12))]
        body += [g.mix(f"(local.get $p{i})") for i in range(6)]
        decl = " ".join(f"(local $p{i} i64)" for i in range(6)) + " (local $t i64)"
        lines.append(f"  (func (export \"e{e}\") (result i64) (local $h i64) {decl}\n    "
                     + "\n    ".join(body) + "\n    (local.get $h))")
    return "\n".join(lines) + ")\n"
