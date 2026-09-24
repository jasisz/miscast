"""Comparison-fusion probes: every integer comparison shape feeding every condition consumer.

Interpreters and baseline compilers fuse a comparison with the instruction that consumes its result
(`cmp` + `select`, `cmp` + `br_if` / `if`, `x != 0` / `eqz` / `eqz eqz` folded into the consumer, `xor` + branch).
Each export evaluates conditions built from `eq` / `ne` / signed and unsigned orderings on i32 and i64, against
zero, boundary constants and other locals, in both operand orders, optionally wrapped in one or two `eqz`, and
feeds them to `select`, `if` with a result, `br_if` with and without a value, and plain value use. Operands come
from a data segment so nothing folds away. The expected i64 hash comes from miscast.watmodel.
"""

import random
import struct

CMPS = ["eq", "ne", "lt_s", "lt_u", "gt_s", "gt_u", "le_s", "le_u", "ge_s", "ge_u"]
BOUND = {"i32": [0, 1, 2, 0x7F, 0x80, 0xFF, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFE],
         "i64": [0, 1, 2, 0xFFFFFFFF, 0x100000000, (1 << 63) - 1, 1 << 63, (1 << 64) - 1, (1 << 64) - 2]}
W = {"i32": 32, "i64": 64}


def _sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class _Gen:
    def __init__(self, r):
        self.r = r
        self.nlab = 0

    def const(self, t):
        r = self.r
        v = r.choice(BOUND[t]) if r.random() < 0.75 else r.getrandbits(W[t])
        return f"({t}.const {_sx(v, W[t])})"

    def operand(self, t):
        r = self.r
        if r.random() < 0.6:
            return f"(local.get ${'a' if t == 'i32' else 'b'}{r.randrange(4)})"
        return self.const(t)

    def cond(self):
        """An i32 condition in one of the shapes that fusion rules look for."""
        r = self.r
        t = r.choice(["i32", "i64"])
        k = r.random()
        x = f"(local.get ${'a' if t == 'i32' else 'b'}{r.randrange(4)})"
        if k < 0.45:
            op = r.choice(CMPS)
            other = f"({t}.const 0)" if r.random() < 0.4 else self.operand(t)
            c = f"({t}.{op} {x} {other})" if r.random() < 0.5 else f"({t}.{op} {other} {x})"
        elif k < 0.60:
            c = f"({t}.eqz {x})"
        elif k < 0.70 and t == "i32":
            c = x  # the raw value as the condition
        elif k < 0.80 and t == "i32":
            c = f"(i32.{r.choice(['xor', 'and', 'or', 'sub'])} {x} {self.operand('i32')})"
        else:
            c = f"({t}.{r.choice(['ne', 'eq'])} {x} {self.operand(t)})"
        for _ in range(r.choice([0, 0, 1, 2])):  # eqz / eqz eqz wrappers
            c = f"(i32.eqz {c})"
        return c

    def value(self, t):
        return self.operand(t)

    def consumer(self):
        """An i64 expression whose value depends on a condition through one consumer shape."""
        r = self.r
        t = r.choice(["i32", "i64"])
        v1, v2, c = self.value(t), self.value(t), self.cond()
        k = r.randrange(5)
        if k == 0:
            e = f"(select {v1} {v2} {c})"
        elif k == 1:
            e = f"(select (result {t}) {v1} {v2} {c})"
        elif k == 2:
            e = f"(if (result {t}) {c} (then {v1}) (else {v2}))"
        elif k == 3:
            self.nlab += 1
            lab = f"$L{self.nlab}"
            e = f"(block {lab} (result {t}) (drop (br_if {lab} {v1} {c})) {v2})"
        else:
            return f"(i64.extend_i32_u {c})"  # the condition itself as a value
        return e if t == "i64" else f"(i64.extend_i32_u {e})"

    def stmt(self):
        r = self.r
        if r.random() < 0.15:
            # br_if without a value, skipping a side effect
            self.nlab += 1
            lab = f"$S{self.nlab}"
            return f"(block {lab} (br_if {lab} {self.cond()}) {self.mix(self.consumer())})"
        return self.mix(self.consumer())

    def mix(self, e):
        return f"(local.set $h (i64.add (i64.mul (local.get $h) (i64.const 0x100000001b3)) {e}))"


def gen_module(seed, n_exports=None):
    r = random.Random(seed ^ 0xC3F5)
    data = bytearray(r.getrandbits(8) for _ in range(256))
    for o in range(0, 256, 8):  # mostly boundary values
        if r.random() < 0.7:
            data[o:o + 8] = struct.pack("<Q", r.choice(BOUND["i64"] + [v | (v << 32) for v in BOUND["i32"]]))
    esc = "".join(f"\\{b:02x}" for b in data)
    lines = ["(module", "  (memory 1)", f"  (data (i32.const 0) \"{esc}\")"]
    for e in range(n_exports or r.randrange(6, 14)):
        g = _Gen(r)
        body = [f"(local.set $a{i} (i32.load (i32.const {r.randrange(0, 248)})))" for i in range(4)]
        body += [f"(local.set $b{i} (i64.load (i32.const {r.randrange(0, 248)})))" for i in range(4)]
        body += [g.stmt() for _ in range(r.randrange(4, 16))]
        decl = " ".join(f"(local $a{i} i32)" for i in range(4)) + " " + " ".join(f"(local $b{i} i64)" for i in range(4))
        lines.append(f"  (func (export \"e{e}\") (result i64) (local $h i64) {decl}\n    "
                     + "\n    ".join(body) + "\n    (local.get $h))")
    return "\n".join(lines) + ")\n"
