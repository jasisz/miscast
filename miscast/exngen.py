"""Random structured control-flow + exception-handling modules for backend differentials.

Targets the newest Winch surface (try_table / throw / throw_ref / catch_ref / catch_all(_ref), only
fuzz-enabled for Winch in Sept 2026) together with the baseline compiler's value-stack bookkeeping:
throws happen with live operands of every type sitting on the wasm operand stack, inside loops, from
nested calls, from dead code, and through re-throws of captured exnrefs. Every function is a random
typed program whose observable result is an i64 hash, so any disagreement between Cranelift, Winch and
Pulley is a backend bug. No float arithmetic (float values only move / reinterpret), so everything is
bit-exact deterministic; all loops are bounded; no traps are generated except none-by-construction.
"""

import random

T = ["i32", "i64", "f32", "f64", "v128"]
TAGS = [[], ["i32"], ["i64"], ["f64"], ["v128"], ["i64", "i32"], ["f32", "v128", "i64"],
        ["i32", "i64", "f32", "f64", "v128"]]
SINGLE = {"i32": 1, "i64": 2, "f64": 3, "v128": 4}


def to_i64(t, e):
    if t == "i32":
        return f"(i64.extend_i32_s {e})"
    if t == "i64":
        return e
    if t == "f32":
        return f"(i64.extend_i32_u (i32.reinterpret_f32 {e}))"
    if t == "f64":
        return f"(i64.reinterpret_f64 {e})"
    return f"(i64.xor (i64x2.extract_lane 0 {e}) (i64.rotl (i64x2.extract_lane 1 {e}) (i64.const 13)))"


def from_i64(t, e):
    if t == "i32":
        return f"(i32.wrap_i64 {e})"
    if t == "i64":
        return e
    if t == "f32":
        return f"(f32.reinterpret_i32 (i32.wrap_i64 {e}))"
    if t == "f64":
        return f"(f64.reinterpret_i64 {e})"
    return f"(i64x2.replace_lane 0 (i64x2.splat {e}) (i64.rotl {e} (i64.const 7)))"


class FGen:
    def __init__(self, r, fi, nhelpers, helper_sigs, is_helper):
        self.r = r
        self.fi = fi
        self.nhelpers = nhelpers
        self.helper_sigs = helper_sigs
        self.locals = {t: [f"$v{t}{k}" for k in range(3)] for t in T}
        self.extra = []  # additional locals (name, type)
        self.labels = []  # stack of result-type lists (innermost last), names
        self.nlab = 0
        self.nloop = 0
        self.budget = 60
        self.is_helper = is_helper
        self.exn = True

    def fresh(self, t):
        n = f"$x{len(self.extra)}"
        self.extra.append((n, t))
        return n

    def lab(self):
        self.nlab += 1
        return f"$L{self.nlab}"

    def mix(self, t, e):
        return f"(local.set $h (i64.add (i64.mul (local.get $h) (i64.const 0x100000001b3)) {to_i64(t, e)}))"

    # -------------------------------------------------------- expressions
    def expr(self, t, d):
        r = self.r
        self.budget -= 1
        if d <= 0 or self.budget <= 0 or r.random() < 0.25:
            return self.leaf(t)
        c = r.random()
        if c < 0.25:
            return self.arith(t, d)
        if c < 0.40 and self.callable_helpers(t):
            j = r.choice(self.callable_helpers(t))
            ps, _ = self.helper_sigs[j]
            args = " ".join(self.expr(p, d - 1) for p in ps)
            return f"(call $g{j} {args})"
        if c < 0.58 and self.exn and (t in SINGLE):
            # try-expression: value from body, or from a single-param tag handler
            tag = SINGLE[t]
            lab = self.lab()
            self.labels.append((lab, [t]))
            body = self.expr(t, d - 1)
            self.labels.pop()
            if r.random() < 0.5:
                fb = self.expr(t, d - 1)
                return (f"(block {lab}o (result {t}) (block {lab}a (br {lab}o (block {lab} (result {t}) "
                        f"(try_table (result {t}) (catch $tag{tag} {lab}) (catch_all {lab}a) {body})))) {fb})")
            return f"(block {lab} (result {t}) (try_table (result {t}) (catch $tag{tag} {lab}) {body}))"
        if c < 0.70 and self.exn and t in SINGLE:
            tag = SINGLE[t]
            return (f"(if (result {t}) {self.cond(d - 1)} (then (throw $tag{tag} {self.expr(t, d - 1)})) "
                    f"(else {self.expr(t, d - 1)}))")
        if c < 0.78:
            return f"(select {self.expr(t, d - 1)} {self.expr(t, d - 1)} {self.cond(d - 1)})" if t != "v128" \
                else f"(select (result v128) {self.expr(t, d - 1)} {self.expr(t, d - 1)} {self.cond(d - 1)})"
        if c < 0.86:
            lab = self.lab()
            self.labels.append((lab, [t]))
            v1 = self.expr(t, d - 1)
            cnd = self.cond(d - 1)
            v2 = self.expr(t, d - 1)
            self.labels.pop()
            return f"(block {lab} (result {t}) (drop (br_if {lab} {v1} {cnd})) {v2})"
        if c < 0.93:
            # a statement-carrying block producing a value
            lab = self.lab()
            self.labels.append((lab, [t]))
            s = self.stmts(d - 1, 2)
            v = self.expr(t, d - 1)
            self.labels.pop()
            return f"(block {lab} (result {t}) {s} {v})"
        return self.arith(t, d)

    def callable_helpers(self, t):
        lo = self.fi + 1 if self.is_helper else 0
        return [j for j in range(lo, self.nhelpers) if self.helper_sigs[j][1] == t]

    def leaf(self, t):
        r = self.r
        c = r.random()
        if c < 0.5:
            return f"(local.get {r.choice(self.locals[t])})"
        if c < 0.6:
            return from_i64(t, "(local.get $h)")
        if c < 0.7:
            return from_i64(t, "(global.get $g)")
        k = r.getrandbits(64) if r.random() < 0.6 else r.choice([0, 1, 2**63, 2**64 - 1, 0x7ff8000000000001])
        if t == "i32":
            return f"(i32.const {k & 0xffffffff})"
        if t == "i64":
            return f"(i64.const {k})"
        return from_i64(t, f"(i64.const {k})")

    def arith(self, t, d):
        r = self.r
        if t == "i32":
            op = r.choice(["add", "sub", "mul", "xor", "rotl", "shr_u", "and", "or"])
            return f"(i32.{op} {self.expr('i32', d - 1)} {self.expr('i32', d - 1)})"
        if t == "i64":
            op = r.choice(["add", "sub", "mul", "xor", "rotl", "shr_s", "and", "or"])
            if r.random() < 0.3:
                src = r.choice(T)
                return to_i64(src, self.expr(src, d - 1))
            return f"(i64.{op} {self.expr('i64', d - 1)} {self.expr('i64', d - 1)})"
        if t in ("f32", "f64"):
            if r.random() < 0.5:
                return f"({t}.{r.choice(['neg', 'abs'])} {self.expr(t, d - 1)})"
            if r.random() < 0.5:
                return f"({t}.copysign {self.expr(t, d - 1)} {self.expr(t, d - 1)})"
            return from_i64(t, self.expr("i64", d - 1))
        op = r.choice(["i64x2.add", "i32x4.sub", "v128.xor", "i8x16.add", "i16x8.mul"])
        if r.random() < 0.3:
            return f"(i64x2.replace_lane {r.randrange(2)} {self.expr('v128', d - 1)} {self.expr('i64', d - 1)})"
        return f"({op} {self.expr('v128', d - 1)} {self.expr('v128', d - 1)})"

    def cond(self, d):
        r = self.r
        if r.random() < 0.5:
            return f"(i32.eqz (i32.and {self.expr('i32', d)} (i32.const {r.choice([1, 3, 7, 15])})))"
        return f"(i64.lt_u (i64.and {self.expr('i64', d)} (i64.const 255)) (i64.const {r.randrange(256)}))"

    # -------------------------------------------------------- statements
    def stmts(self, d, n):
        return " ".join(self.stmt(d) for _ in range(n))

    def stmt(self, d):
        r = self.r
        self.budget -= 1
        c = r.random()
        if d <= 0 or self.budget <= 0 or c < 0.25:
            t = r.choice(T)
            if r.random() < 0.5:
                return f"(local.set {r.choice(self.locals[t])} {self.expr(t, max(d, 1))})"
            return self.mix(t, self.expr(t, max(d, 1)))
        if c < 0.35:
            return f"(if {self.cond(d - 1)} (then {self.stmts(d - 1, 2)}) (else {self.stmts(d - 1, 1)}))"
        if c < 0.45:
            self.nloop += 1
            i = f"$i{self.nloop}"
            self.extra.append((i, "i32"))
            lab = self.lab()
            self.labels.append((lab, None))  # loop label: never a br target (would skip the counter)
            body = self.stmts(d - 1, 2)
            self.labels.pop()
            return (f"(local.set {i} (i32.const {r.randrange(1, 5)})) (loop {lab} {body} "
                    f"(br_if {lab} (local.tee {i} (i32.sub (local.get {i}) (i32.const 1)))))")
        if c < 0.78 and not self.exn:
            return self.alt_stmt(d)
        if c < 0.70:
            return self.try_stmt(d)
        if c < 0.78:
            tag = r.randrange(len(TAGS))
            vals = " ".join(self.expr(t, d - 1) for t in TAGS[tag])
            return f"(if {self.cond(d - 1)} (then (throw $tag{tag} {vals})))"
        if c < 0.84:
            # branch out of an enclosing statement block
            cands = [(l, ty) for l, ty in self.labels if ty == []]
            if cands:
                l, _ = r.choice(cands)
                return f"(br_if {l} {self.cond(d - 1)})"
            return self.mix("i64", self.expr("i64", d - 1))
        if c < 0.90:
            lab = self.lab()
            self.labels.append((lab, []))
            s = self.stmts(d - 1, 3)
            self.labels.pop()
            return f"(block {lab} {s})"
        if c < 0.94:
            # dead code after an unconditional branch/throw inside a block
            lab = self.lab()
            self.labels.append((lab, []))
            dead_kind = r.choice(["br", "throw", "unreachable-guarded"] if self.exn else ["br", "unreachable-guarded"])
            if dead_kind == "br":
                term = f"(br {lab})"
            elif dead_kind == "throw":
                term = f"(throw $tag0)"
            else:
                term = f"(br {lab})"
            dead = self.stmts(d - 1, 2)
            self.labels.pop()
            s = f"(block {lab} {term} {dead})"
            if dead_kind == "throw":
                return f"(block {lab}c (try_table (catch $tag0 {lab}c) {s}))"
            return s
        return self.mix("i64", self.expr("i64", d - 1))

    def alt_stmt(self, d):
        """Replacement for exception statements when exceptions are disabled (overridden in subclasses)."""
        return self.mix("i64", self.expr("i64", d - 1))

    def try_stmt(self, d):
        r = self.r
        k = r.randrange(1, 4)
        clauses = []
        for _ in range(k):
            kind = r.choice(["catch", "catch", "catch_ref", "catch_all", "catch_all_ref"])
            tag = r.randrange(len(TAGS))
            ty = list(TAGS[tag]) if kind.startswith("catch") and "all" not in kind else []
            if kind.endswith("_ref"):
                ty = ty + ["exnref"]
            clauses.append((kind, tag, ty))
        done = self.lab()
        names = [self.lab() for _ in clauses]
        self.labels.append((done, []))
        # body sees the handler labels too (as br targets they need values; we only target done/[])
        body = self.stmts(d - 1, r.randrange(1, 4))
        self.labels.pop()
        cl = " ".join(
            f"({kind} $tag{tag} {nm})" if "all" not in kind else f"({kind} {nm})"
            for (kind, tag, ty), nm in zip(clauses, names))
        inner = f"(try_table {cl} {body}) (br {done})"
        # wrap handler blocks: innermost = first clause
        for (kind, tag, ty), nm in zip(clauses, names):
            res = f"(result {' '.join(ty)})" if ty else ""
            handler = self.handler(ty, d)
            inner = f"(block {nm} {res} {inner}) {handler} (br {done})"
        return f"(block {done} {inner})"

    def handler(self, ty, d):
        """Consume the handler's values (on the stack in order) and run a little code."""
        r = self.r
        out = []
        for t in reversed(ty):
            if t == "exnref":
                n = self.fresh("exnref")
                out.append(f"(local.set {n})")
                if r.random() < 0.3:
                    # re-throw it (propagates to an enclosing handler / the export boundary)
                    out.append(f"(if {self.cond(0)} (then (throw_ref (local.get {n}))))")
            else:
                n = self.fresh(t)
                out.append(f"(local.set {n})")
                out.append(self.mix(t, f"(local.get {n})"))
        out.append(f"(local.set $h (i64.xor (local.get $h) (i64.const {r.getrandbits(32)})))")
        if r.random() < 0.5:
            out.append(self.stmt(max(d - 2, 0)))
        return " ".join(out)


def gen_module(seed):
    r = random.Random(seed)
    nh = r.randrange(1, 6)
    helper_sigs = []
    for _ in range(nh):
        ps = [r.choice(T) for _ in range(r.choice([0, 1, 2, 3, 6, 10]))]
        helper_sigs.append((ps, r.choice(T)))
    lines = ["(module", "  (global $g (mut i64) (i64.const 0x1234))"]
    for i, ps in enumerate(TAGS):
        lines.append(f"  (tag $tag{i} (param {' '.join(ps)}))")

    def func(header, g, params, result_t, depth):
        locs = []
        for t in T:
            for n in g.locals[t]:
                locs.append(f"(local {n} {t})")
        pre = []
        for pi, pt in enumerate(params):
            pre.append(g.mix(pt, f"(local.get {pi})"))
        body = g.stmts(depth, r.randrange(2, 6))
        tail = f"(global.set $g (i64.xor (global.get $g) (local.get $h))) {from_i64(result_t, '(local.get $h)')}" \
            if result_t else ""
        extra = " ".join(f"(local {n} {t})" for n, t in g.extra)
        return (f"  {header}\n    (local $h i64) {' '.join(locs)} {extra}\n    "
                + " ".join(pre) + f"\n    {body}\n    {tail})")

    for j, (ps, rt) in enumerate(helper_sigs):
        g = FGen(r, j, nh, helper_sigs, True)
        g.budget = 40
        header = f"(func $g{j} (param {' '.join(ps)}) (result {rt})"
        lines.append(func(header, g, ps, rt, r.randrange(1, 4)))
    for e in range(r.randrange(2, 6)):
        g = FGen(r, -1, nh, helper_sigs, False)
        g.budget = 90
        header = f"(func $e{e}"
        lines.append(func(header, g, [], "i64", r.randrange(2, 5)).replace(
            f"(func $e{e}", f"(func $e{e} (result i64)", 1))
        # exported wrapper catches everything escaping so the export result stays comparable
        lines.append(
            f"  (func (export \"e{e}\") (result i64) (block $c (result exnref) "
            f"(return (try_table (result i64) (catch_all_ref $c) (call $e{e})))) (drop) "
            f"(i64.xor (global.get $g) (i64.const -1)))")
    return "\n".join(lines) + ")\n"
