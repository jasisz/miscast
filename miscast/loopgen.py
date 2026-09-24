"""Random loop nests for backend differentials, aimed at loop-invariant code motion (Cranelift O2 vs O0, where
the egraph optimizer and its LICM are off, plus Pulley).

Each export runs nested counted loops whose bodies mix truly invariant work (pure arithmetic over locals the loop
never writes, an immutable GC field, `array.len`) with work that only looks invariant: loads from a fixed memory
address, a global, or a mutable GC field, next to stores and helper calls that change them. Bodies contain blocks
that stay in the loop and blocks that leave it (`br` out of the loop), the shapes where elaboration decides what
may be hoisted. Field reads through a nullable reference are guarded by `ref.is_null` inside the loop. Every loop
is counted, nothing traps, and every value flows into an i64 hash.
"""

import random

MEM_BYTES = 4096


class _Gen:
    def __init__(self, r, alloc=False):
        self.r = r
        self.alloc = alloc
        self.nloop = 0
        self.counters = []  # loop counter locals (i32)

    def inv(self, d=2):
        """i64 expression over the pool locals $p0..$p3, which loops never write."""
        r = self.r
        if d <= 0 or r.random() < 0.3:
            return r.choice([f"(local.get $p{r.randrange(4)})", f"(i64.const {r.getrandbits(40)})"])
        op = r.choice(["add", "mul", "xor", "rotl", "shr_u", "and", "sub"])
        return f"(i64.{op} {self.inv(d - 1)} {self.inv(d - 1)})"

    def looks_inv(self):
        """Reads that look loop-invariant but that stores / calls inside the loop may change."""
        r = self.r
        return r.choice([
            f"(i64.load (i32.const {r.choice([0, 8, 64])}))",
            "(global.get $g)",
            "(struct.get $S 1 (local.get $s))",
            "(array.get $A (local.get $a) (i32.const 1))",
            # truly invariant GC reads: immutable field, array length
            "(struct.get $S 0 (local.get $s))",
            "(i64.extend_i32_u (array.len (local.get $a)))",
        ])

    def variant(self):
        c = self.r.choice(self.counters)
        return f"(i64.extend_i32_u (local.get {c}))"

    def mix(self, e):
        return f"(local.set $h (i64.add (i64.mul (local.get $h) (i64.const 0x100000001b3)) {e}))"

    def clobber(self):
        """A write that changes one of the look-invariant sources."""
        r = self.r
        v = f"(i64.xor (local.get $h) {self.variant()})"
        return r.choice([
            f"(i64.store (i32.const {r.choice([0, 8, 64])}) {v})",
            f"(global.set $g {v})",
            f"(struct.set $S 1 (local.get $s) {v})",
            f"(array.set $A (local.get $a) (i32.const 1) {v})",
            f"(call $poke {v})",
        ])

    def guarded(self):
        """A field read through the nullable $n, guarded inside the loop."""
        return ("(if (i32.eqz (ref.is_null (local.get $n))) (then "
                + self.mix("(struct.get $S 1 (local.get $n))") + "))")

    def alloc_stmt(self):
        """Allocation inside the loop: garbage that triggers collections, or a fresh object replacing $s / $a
        (so even the immutable field and the length stop being loop-invariant)."""
        r = self.r
        k = r.randrange(4)
        if k == 0:
            return f"(drop (array.new $A (local.get $h) (i32.const {r.choice([16, 1000, 50000])})))"
        if k == 1:
            return f"(local.set $s (struct.new $S {self.variant()} (local.get $h)))"
        if k == 2:
            return "(local.set $n (if (result (ref null $S)) (i32.wrap_i64 (i64.and (local.get $h) (i64.const 1)))" \
                   " (then (local.get $s)) (else (ref.null $S))))"
        return f"(local.set $a (array.new $A (local.get $h) (i32.const {r.randrange(2, 9)})))"

    def stmt(self, depth, labels):
        r = self.r
        if self.alloc and r.random() < 0.2:
            return self.alloc_stmt()
        c = r.random()
        if c < 0.25:
            return self.mix(self.inv())
        if c < 0.45:
            return self.mix(self.looks_inv())
        if c < 0.60:
            return self.clobber()
        if c < 0.68:
            return self.guarded()
        if c < 0.78:
            return self.mix(f"(i64.add {self.inv(1)} {self.variant()})")
        if c < 0.88 and labels:
            # a block that leaves the loop: invariant work computed only on the exit path
            out = r.choice(labels)
            cnd = f"(i64.eqz (i64.and {self.variant()} (i64.const {r.choice([1, 3, 7])})))"
            return f"(if {cnd} (then {self.mix(self.inv())} {self.mix(self.looks_inv())} (br {out})))"
        if depth < 3:
            return self.loop(depth + 1, labels)
        return self.mix(self.inv())

    def loop(self, depth, labels):
        r = self.r
        self.nloop += 1
        k = self.nloop
        c = f"$c{k}"
        self.counters.append(c)
        out = f"$o{k}"
        body = " ".join(self.stmt(depth, labels + [out]) for _ in range(r.randrange(2, 7)))
        n = r.randrange(1, 6)
        return (f"(block {out} (local.set {c} (i32.const {n})) (loop $l{k} {body} "
                f"(br_if $l{k} (local.tee {c} (i32.sub (local.get {c}) (i32.const 1))))))")


def gen_module(seed, n_exports=None, alloc=False):
    r = random.Random(seed ^ 0x100B)
    data = "".join(f"\\{r.getrandbits(8):02x}" for _ in range(256))
    lines = ["(module",
             "  (type $S (struct (field i64) (field (mut i64))))",
             "  (type $A (array (mut i64)))",
             "  (memory 1)",
             f"  (data (i32.const 0) \"{data}\")",
             "  (global $g (mut i64) (i64.const 0x5eed))",
             "  (func $poke (param i64) (i64.store (i32.const 8) (local.get 0))"
             " (global.set $g (i64.rotl (global.get $g) (local.get 0))))"]
    for e in range(n_exports or r.randrange(3, 8)):
        g = _Gen(r, alloc)
        body = [f"(local.set $p{i} (i64.load (i32.const {r.randrange(0, 240)})))" for i in range(4)]
        body.append(f"(local.set $s (struct.new $S (local.get $p0) (i64.const {r.getrandbits(32)})))")
        body.append(f"(local.set $a (array.new $A (local.get $p1) (i32.const {r.randrange(2, 9)})))")
        if r.random() < 0.5:
            body.append("(local.set $n (local.get $s))")
        body.append(g.loop(1, []))
        # fold the final state of everything the loops could have changed
        body.append(g.mix("(i64.load (i32.const 0))"))
        body.append(g.mix("(i64.load (i32.const 8))"))
        body.append(g.mix("(i64.load (i32.const 64))"))
        body.append(g.mix("(global.get $g)"))
        body.append(g.mix("(struct.get $S 1 (local.get $s))"))
        body.append(g.mix("(array.get $A (local.get $a) (i32.const 1))"))
        locs = ("(local $h i64) (local $p0 i64) (local $p1 i64) (local $p2 i64) (local $p3 i64) "
                "(local $s (ref null $S)) (local $n (ref null $S)) (local $a (ref null $A)) "
                + " ".join(f"(local {c} i32)" for c in g.counters))
        lines.append(f"  (func (export \"e{e}\") (result i64) {locs}\n    " + "\n    ".join(body)
                     + "\n    (local.get $h))")
    return "\n".join(lines) + ")\n"


def gen_module_alloc(seed):
    """Loop nests that also allocate inside the loops (collections mid-loop, objects replaced under the loop)."""
    return gen_module(seed, alloc=True)
