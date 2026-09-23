"""Random linear-memory bounds-check modules for backend differentials (Cranelift vs Winch vs Pulley, and
across memory configurations: guard pages vs explicit bounds checks, signal-based vs explicit traps).

Each export runs a straight-line / looping program of loads and stores whose addresses sit on or near the
end of memory: constants just below / at / past the edge, edges computed at run time from `memory.size`,
addresses derived from loaded bytes, large `offset=` immediates, and loops striding toward the edge. Some
loops call a helper that runs `memory.grow`, so a bound or base pointer hoisted out of the loop becomes
stale. Modules mix memory64 and multiple memories, plus `memory.fill` / `memory.copy` near the edge. Loaded
values fold into an i64 hash; an out-of-bounds access traps, and wtdiff compares trap codes too.

A partially out-of-bounds store may leave its in-bounds bytes written on some platforms (wasmtime allows
this), so every export begins by filling each memory with a fixed byte: nothing an earlier trapping export
tore is ever observed. Exports run in module order on one instance, so `memory.grow` state carries over
identically in every configuration.
"""

import random

PAGE = 65536
LOADS = {"i32": [("i32.load", 4), ("i32.load8_s", 1), ("i32.load8_u", 1), ("i32.load16_s", 2),
                 ("i32.load16_u", 2)],
         "i64": [("i64.load", 8), ("i64.load8_u", 1), ("i64.load16_s", 2), ("i64.load32_s", 4),
                 ("i64.load32_u", 4)],
         "v128": [("v128.load", 16), ("v128.load64_splat", 8), ("v128.load32_zero", 4),
                  ("v128.load8x8_s", 8)]}
STORES = {"i32": [("i32.store", 4), ("i32.store8", 1), ("i32.store16", 2)],
          "i64": [("i64.store", 8), ("i64.store32", 4), ("i64.store16", 2)],
          "v128": [("v128.store", 16)]}


class _Mem:
    def __init__(self, idx, is64, init, maxp):
        self.name = f"$m{idx}"
        self.is64 = is64
        self.at = "i64" if is64 else "i32"
        self.init = init
        self.maxp = maxp

    def decl(self):
        return f"(memory {self.name} {'i64 ' if self.is64 else ''}{self.init} {self.maxp})"

    def const(self, v):
        return f"({self.at}.const {v})"

    def size_bytes(self):
        return f"({self.at}.mul (memory.size {self.name}) {self.const(PAGE)})"


class _FGen:
    def __init__(self, r, mems, grow_helpers):
        self.r = r
        self.mems = mems
        self.grow_helpers = grow_helpers
        self.nloop = 0
        self.extra = []

    def mix(self, t, e):
        if t == "i32":
            v = f"(i64.extend_i32_u {e})"
        elif t == "i64":
            v = e
        else:
            v = f"(i64.xor (i64x2.extract_lane 0 {e}) (i64.rotl (i64x2.extract_lane 1 {e}) (i64.const 23)))"
        return f"(local.set $h (i64.add (i64.mul (local.get $h) (i64.const 0x100000001b3)) {v}))"

    def addr(self, m, width, oob_p=0.1):
        """(address expr, offset immediate) mostly *just inside* the current end of memory `m`; with
        probability `oob_p` the access straddles or passes the edge and must trap."""
        r = self.r
        oob = r.random() < oob_p
        c = r.random()
        off = r.choice([0, 0, 0, 1, 7, width, 16, 4096])
        # distance from the end to the access start: >= width stays in bounds, < width straddles the edge
        k = (r.choice([0, 1, width - 1]) if oob else width + r.choice([0, 0, 1, 7, 64, 4095])) + off
        if c < 0.40:  # run-time edge
            return f"({m.at}.sub {m.size_bytes()} {m.const(k)})", off
        if c < 0.60:  # static edge of the *initial* size (memory only grows, so this stays in bounds)
            return m.const(max(m.init * PAGE - k, 0)), off
        if c < 0.75:  # data-dependent: a loaded byte picks a spot in the last 256 + k bytes
            src = r.choice(self.mems)
            b = f"(i32.load8_u {src.name} {src.const(r.randrange(64))})"
            if m.is64:
                b = f"(i64.extend_i32_u {b})"
            return f"({m.at}.sub {m.size_bytes()} ({m.at}.add {b} {m.const(k)}))", off
        if oob and c < 0.85:  # far past the end: 4 GiB-ish addresses / offsets
            if m.is64:
                return m.const(r.choice([1 << 32, (1 << 32) - 1, (1 << 33) + 5, (1 << 63) - 1])), 0
            return m.const(r.choice([0, 1, 255])), (1 << 32) - 1
        return m.const(r.randrange(0, m.init * PAGE - 4200)), off

    def value(self, t):
        r = self.r
        k = r.getrandbits(64)
        if t == "i32":
            return f"(i32.wrap_i64 (i64.xor (local.get $h) (i64.const {k})))"
        if t == "i64":
            return f"(i64.rotl (local.get $h) (i64.const {k % 64}))"
        return f"(i64x2.replace_lane 1 (i64x2.splat (local.get $h)) (i64.const {k}))"

    def access(self):
        r = self.r
        m = r.choice(self.mems)
        t = r.choice(["i32", "i32", "i64", "i64", "v128"])
        if r.random() < 0.55:
            op, w = r.choice(LOADS[t])
            a, off = self.addr(m, w)
            return self.mix(t, f"({op} {m.name} offset={off} {a})")
        op, w = r.choice(STORES[t])
        a, off = self.addr(m, w)
        return f"({op} {m.name} offset={off} {a} {self.value(t)})"

    def bulk(self):
        r = self.r
        m = r.choice(self.mems)
        n = r.choice([0, 1, 16, 255, 4096])
        if r.random() < 0.5:
            a, _ = self.addr(m, max(n, 1))
            return f"(memory.fill {m.name} {a} (i32.const {r.randrange(256)}) {m.const(n)})"
        s = r.choice(self.mems)
        at = "i64" if (m.is64 and s.is64) else "i32"  # dst/src use their memory's index type, len the smaller
        d, _ = self.addr(m, max(n, 1))
        sa, _ = self.addr(s, max(n, 1))
        return f"(memory.copy {m.name} {s.name} {d} {sa} ({at}.const {n}))"

    def grow(self):
        m = self.r.choice(self.mems)
        g = f"(memory.grow {m.name} {m.const(self.r.choice([0, 1, 1, 2]))})"
        return self.mix("i64", g if m.is64 else f"(i64.extend_i32_s {g})")

    def loop(self):
        """Stride toward (and possibly past) the edge; optionally grow inside the loop via a helper."""
        r = self.r
        m = r.choice(self.mems)
        self.nloop += 1
        i = f"$i{self.nloop}"
        self.extra.append((i, m.at))
        t = r.choice(["i32", "i64", "v128"])
        op, w = r.choice(LOADS[t])
        sop, sw = r.choice(STORES[t])
        stride = r.choice([1, w, 8, 64, 4096, PAGE // 2])
        n = r.randrange(2, 12)
        span = (n - 1) * stride + max(w, sw) + w  # last iteration's furthest byte, store offset <= w
        cross = r.random() < 0.25  # step over the initial edge (traps unless a helper grew the memory)
        start = max(m.init * PAGE - span + (r.choice([1, w, stride]) if cross else -r.choice([0, 0, 1, 64])), 0)
        body = [self.mix(t, f"({op} {m.name} (local.get {i}))"),
                f"({sop} {m.name} offset={r.choice([0, 1, w])} (local.get {i}) {self.value(t)})"]
        if self.grow_helpers and r.random() < 0.5:
            g = r.choice(self.grow_helpers)
            body.insert(r.randrange(len(body) + 1), f"(local.set $h (i64.xor (local.get $h) (call {g})))")
        cnt = f"$c{self.nloop}"
        self.extra.append((cnt, "i32"))
        return (f"(local.set {i} {m.const(start)}) (local.set {cnt} (i32.const {n})) "
                f"(loop $L{self.nloop} {' '.join(body)} "
                f"(local.set {i} ({m.at}.add (local.get {i}) {m.const(stride)})) "
                f"(br_if $L{self.nloop} (local.tee {cnt} (i32.sub (local.get {cnt}) (i32.const 1)))))")

    def stmt(self):
        c = self.r.random()
        if c < 0.55:
            return self.access()
        if c < 0.68:
            return self.bulk()
        if c < 0.76:
            return self.grow()
        return self.loop()


def gen_module(seed, n_exports=None):
    r = random.Random(seed ^ 0x3E3B0)
    mems = []
    for mi in range(r.choice([1, 1, 2, 3])):
        init = r.choice([1, 1, 2, 3])
        mems.append(_Mem(mi, r.random() < 0.35, init, init + r.choice([0, 1, 2, 4])))
    lines = ["(module"] + [f"  {m.decl()}" for m in mems]
    helpers = []
    for hi in range(r.randrange(0, 3)):
        m = r.choice(mems)
        d = m.const(r.choice([0, 1]))
        g = f"(memory.grow {m.name} {d})"
        g = g if m.is64 else f"(i64.extend_i32_s {g})"
        lines.append(f"  (func $grow{hi} (result i64) {g})")
        helpers.append(f"$grow{hi}")
    for e in range(n_exports or r.randrange(4, 10)):
        g = _FGen(r, mems, helpers)
        body = [f"(memory.fill {m.name} {m.const(0)} (i32.const {0x5A ^ e}) {m.size_bytes()})" for m in mems]
        body += [g.stmt() for _ in range(r.randrange(3, 14))]
        locs = " ".join(f"(local {n} {t})" for n, t in g.extra)
        lines.append(f"  (func (export \"e{e}\") (result i64) (local $h i64) {locs}\n    "
                     + "\n    ".join(body) + "\n    (local.get $h))")
    return "\n".join(lines) + ")\n"
