"""GC alias-region generator: random straight-line / branchy / looping programs that read and write the
same GC objects through *different static types*, with a Python interpreter as the oracle.

Wasmtime (Sept 2026) gives every GC struct field and array-element set its own Cranelift alias region,
keyed by the supertype that first introduced the field, so redundant-load elimination and store-to-load
forwarding can now move across GC accesses. A region mismatch between two accesses of the same memory
(a subtype view vs a supertype view, a deduplicated structural twin, an inlined helper, an array.copy or
array.fill on an aliased array) silently returns a stale value. This mode aims at exactly that: objects
are reached through casts to every legal static type, repointed through locals, a holder struct and a
ref-array, mutated by (possibly inlined) helpers, and mixed into an i64 checksum the model computes.

Every program is trap-free and deterministic, so any deviation from the model's checksum is a
miscompile — no second engine is needed.
"""

import random

M64 = (1 << 64) - 1

# struct families. dyn type -> ordered chain (self first) and field widths.
_FIELDS = {
    "A": ["i32", "i64"],
    "B": ["i32", "i64", "i32"],
    "C": ["i32", "i64", "i32", "i8"],
    "D": ["i32", "i64", "i16"],
    "P": ["i32"],
    "P2": ["i32"],
    "Q": ["i32", "i32"],
}
_CHAIN = {  # static types usable for a dynamic type (itself + supertypes, incl. canonical twins)
    "A": ["A"], "B": ["B", "A"], "C": ["C", "B", "A"], "D": ["D", "A"],
    "P": ["P", "P2"], "Q": ["Q", "P2", "P"],
}
_FAM = {"A": "a", "B": "a", "C": "a", "D": "a", "P": "p", "Q": "p"}
_ARR = {"V": "i32", "W": "i32", "V8": "i8"}
_ACHAIN = {"V": ["V"], "W": ["W", "V"], "V8": ["V8"]}
_AFAM = {"V": "v", "W": "v", "V8": "b"}
_LOCALS = {"a": ["a0", "a1", "a2", "a3"], "p": ["p0", "p1", "p2"], "v": ["v0", "v1", "v2"], "b": ["b0", "b1"]}
_LTYPE = {"a": "A", "p": "P", "v": "V", "b": "V8"}

TYPES = """  (rec
    (type $A (sub (struct (field (mut i32)) (field (mut i64)))))
    (type $B (sub $A (struct (field (mut i32)) (field (mut i64)) (field (mut i32)))))
    (type $C (sub $B (struct (field (mut i32)) (field (mut i64)) (field (mut i32)) (field (mut i8)))))
    (type $D (sub $A (struct (field (mut i32)) (field (mut i64)) (field (mut i16))))))
  (type $P (sub (struct (field (mut i32)))))
  (type $P2 (sub (struct (field (mut i32)))))
  (type $Q (sub $P2 (struct (field (mut i32)) (field (mut i32)))))
  (type $V (sub (array (mut i32))))
  (type $W (sub $V (array (mut i32))))
  (type $V8 (sub (array (mut i8))))
  (type $R (array (mut (ref null $A))))
  (type $H (struct (field (mut (ref null $A))) (field (mut (ref null $V)))))
"""

HELPERS = """  (func $hsetA0 (param $o (ref $A)) (param $v i32) (struct.set $A 0 (local.get $o) (local.get $v)))
  (func $hsetA1 (param $o (ref null $A)) (param $v i64) (struct.set $A 1 (local.get $o) (local.get $v)))
  (func $hgetA0 (param $o (ref $A)) (result i32) (struct.get $A 0 (local.get $o)))
  (func $hsetP (param $o (ref $P2)) (param $v i32) (struct.set $P2 0 (local.get $o) (local.get $v)))
  (func $hsetV (param $a (ref $V)) (param $i i32) (param $v i32)
    (array.set $V (local.get $a) (local.get $i) (local.get $v)))
  (func $hcopyV (param $d (ref $V)) (param $s (ref $V))
    (array.copy $V $V (local.get $d) (i32.const 1) (local.get $s) (i32.const 0) (i32.const 3)))
"""


def _wrap32(x):
    return x & 0xFFFFFFFF


def _sx(x, bits):
    x &= (1 << bits) - 1
    return x - (1 << bits) if x >> (bits - 1) else x


def _store(width, v):
    """Value as stored in a field of `width` (raw unsigned bits)."""
    return v & {"i8": 0xFF, "i16": 0xFFFF, "i32": 0xFFFFFFFF, "i64": M64}[width]


class _Gen:
    def __init__(self, rng):
        self.r = rng
        self.dyn = {}  # local -> dyn type at generation time (top level exact)

    # --- operand choices -------------------------------------------------
    def pick_struct_local(self):
        fam = self.r.choice(["a", "a", "a", "p"])
        return fam, self.r.choice(_LOCALS[fam])

    def pick_array_local(self):
        fam = self.r.choice(["v", "v", "b"])
        return fam, self.r.choice(_LOCALS[fam])

    def gen_op(self, top):
        r = self.r
        kinds = ["sget", "sget", "sset", "sset", "sset", "aget", "aset", "aset", "afill", "acopy", "alen",
                 "call", "mixget"]
        if top:
            kinds += ["repoint", "repoint", "hstore", "hload", "rstore", "rload", "new", "if", "loop"]
        k = r.choice(kinds)
        if k in ("sget", "sset", "mixget"):
            fam, loc = self.pick_struct_local()
            dyn = self.dyn[loc]
            f = r.randrange(len(_FIELDS[dyn]))
            st = r.choice([t for t in _CHAIN[dyn] if f < len(_FIELDS[t])])
            if k == "sget":
                return ("sget", loc, st, f, r.choice(["s", "u"]))
            if k == "mixget":  # same field through two different static views, back to back
                st2 = r.choice([t for t in _CHAIN[dyn] if f < len(_FIELDS[t])])
                return ("mixget", loc, st, st2, f, r.randrange(1 << 16))
            return ("sset", loc, st, f, r.randrange(1 << 32))
        if k in ("aget", "aset", "afill", "alen"):
            fam, loc = self.pick_array_local()
            dyn = self.dyn[loc]
            st = r.choice(_ACHAIN[dyn])
            idx = ("c", r.randrange(4)) if r.random() < 0.6 else ("acc",)
            if k == "aget":
                return ("aget", loc, st, idx, r.choice(["s", "u"]))
            if k == "aset":
                return ("aset", loc, st, idx, r.randrange(1 << 32))
            if k == "alen":
                return ("alen", loc, st)
            off = r.randrange(4)
            n = r.randrange(0, 5 - off)
            return ("afill", loc, st, off, r.randrange(1 << 32), n)
        if k == "acopy":
            fam = r.choice(["v", "v", "b"])
            d, s = r.choice(_LOCALS[fam]), r.choice(_LOCALS[fam])
            dt, stt = r.choice(_ACHAIN[self.dyn[d]]), r.choice(_ACHAIN[self.dyn[s]])
            doff, soff = r.randrange(4), r.randrange(4)
            n = r.randrange(0, 5 - max(doff, soff))
            return ("acopy", d, dt, doff, s, stt, soff, n)
        if k == "call":
            h = r.choice(["hsetA0", "hsetA1", "hgetA0", "hsetP", "hsetV", "hcopyV"])
            if h in ("hsetA0", "hsetA1", "hgetA0"):
                return ("call", h, r.choice(_LOCALS["a"]), None, r.randrange(1 << 32))
            if h == "hsetP":
                return ("call", h, r.choice(_LOCALS["p"]), None, r.randrange(1 << 32))
            if h == "hsetV":
                return ("call", h, r.choice(_LOCALS["v"]), r.randrange(4), r.randrange(1 << 32))
            return ("call", h, r.choice(_LOCALS["v"]), r.choice(_LOCALS["v"]), 0)
        if k == "repoint":
            fam = r.choice(list(_LOCALS))
            d, s = r.choice(_LOCALS[fam]), r.choice(_LOCALS[fam])
            self.dyn[d] = self.dyn[s]
            return ("repoint", d, s)
        if k == "hstore":
            return ("hstore", r.choice(_LOCALS["a"] + _LOCALS["v"]))
        if k == "hload":
            fam = r.choice(["a", "v"])
            d = r.choice(_LOCALS[fam])
            self.dyn[d] = self.hold[0 if fam == "a" else 1]
            return ("hload", d)
        if k == "rstore":
            return ("rstore", r.choice(_LOCALS["a"]), r.randrange(4))
        if k == "rload":
            i = r.randrange(4)
            d = r.choice(_LOCALS["a"])
            self.dyn[d] = self.rarr[i]
            return ("rload", d, i)
        if k == "new":
            fam = r.choice(list(_LOCALS))
            d = r.choice(_LOCALS[fam])
            dyn = r.choice({"a": "ABCD", "p": ["P", "Q"], "v": ["V", "W"], "b": ["V8"]}[fam])
            self.dyn[d] = dyn
            return ("new", d, dyn, [r.randrange(1 << 32) for _ in range(4)])
        if k == "if":
            return ("if", r.randrange(1, 8), [self.gen_op(False) for _ in range(r.randrange(1, 5))],
                    [self.gen_op(False) for _ in range(r.randrange(0, 4))])
        if k == "loop":
            return ("loop", r.randrange(1, 5), [self.gen_op(False) for _ in range(r.randrange(1, 6))])
        raise AssertionError(k)

    # hold / rarr dyn-type tracking for hload/rload at generation time
    def track_store(self, op):
        if op[0] == "hstore":
            loc = op[1]
            self.hold[0 if loc[0] == "a" else 1] = self.dyn[loc]
        elif op[0] == "rstore":
            self.rarr[op[2]] = self.dyn[op[1]]


# ---------------------------------------------------------------- emission

def _ref(loc, st):
    # locals are typed with the family root; cast to the chosen static view (non-null).
    return f"(ref.cast (ref ${st}) (local.get ${loc}))"


_ACC = "(local.get $acc)"


def _mix(val_i64):
    return f"(local.set $acc (i64.add (i64.mul {_ACC} (i64.const 31)) {val_i64}))"


def _field_get(loc, st, f, ext):
    w = _FIELDS[st][f]
    if w == "i64":
        return f"(struct.get ${st} {f} {_ref(loc, st)})"
    op = "struct.get" if w == "i32" else f"struct.get_{ext}"
    return f"(i64.extend_i32_s ({op} ${st} {f} {_ref(loc, st)}))"


def _set_val(w, c):
    if w == "i64":
        return f"(i64.xor {_ACC} (i64.const {_sx(c, 32)}))"
    return f"(i32.xor (i32.wrap_i64 {_ACC}) (i32.const {_sx(c, 32)}))"


def _idx(idx, loc, st):
    if idx[0] == "c":
        return f"(i32.const {idx[1]})"
    return f"(i32.rem_u (i32.wrap_i64 {_ACC}) (array.len (local.get ${loc})))"


def emit(op):
    k = op[0]
    if k == "sget":
        _, loc, st, f, ext = op
        return _mix(_field_get(loc, st, f, ext))
    if k == "mixget":
        _, loc, st, st2, f, c = op
        return (f"(struct.set ${st} {f} {_ref(loc, st)} "
                f"{_set_val(_FIELDS[st][f], c)})\n    {_mix(_field_get(loc, st2, f, 's'))}")
    if k == "sset":
        _, loc, st, f, c = op
        return f"(struct.set ${st} {f} {_ref(loc, st)} {_set_val(_FIELDS[st][f], c)})"
    if k == "aget":
        _, loc, st, idx, ext = op
        g = "array.get" if _ARR[st] == "i32" else f"array.get_{ext}"
        return _mix(f"(i64.extend_i32_s ({g} ${st} {_ref(loc, st)} {_idx(idx, loc, st)}))")
    if k == "aset":
        _, loc, st, idx, c = op
        return f"(array.set ${st} {_ref(loc, st)} {_idx(idx, loc, st)} {_set_val('i32', c)})"
    if k == "alen":
        _, loc, st = op
        return _mix(f"(i64.extend_i32_u (array.len {_ref(loc, st)}))")
    if k == "afill":
        _, loc, st, off, c, n = op
        return f"(array.fill ${st} {_ref(loc, st)} (i32.const {off}) {_set_val('i32', c)} (i32.const {n}))"
    if k == "acopy":
        _, d, dt, doff, s, stt, soff, n = op
        return (f"(array.copy ${dt} ${stt} {_ref(d, dt)} (i32.const {doff}) {_ref(s, stt)} "
                f"(i32.const {soff}) (i32.const {n}))")
    if k == "call":
        _, h, loc, extra, c = op
        if h == "hsetA0":
            return f"(call $hsetA0 (ref.as_non_null (local.get ${loc})) {_set_val('i32', c)})"
        if h == "hsetA1":
            return f"(call $hsetA1 (local.get ${loc}) {_set_val('i64', c)})"
        if h == "hgetA0":
            return _mix(f"(i64.extend_i32_s (call $hgetA0 (ref.as_non_null (local.get ${loc}))))")
        if h == "hsetP":
            return f"(call $hsetP (ref.as_non_null (local.get ${loc})) {_set_val('i32', c)})"
        if h == "hsetV":
            return f"(call $hsetV (ref.as_non_null (local.get ${loc})) (i32.const {extra}) {_set_val('i32', c)})"
        return f"(call $hcopyV (ref.as_non_null (local.get ${loc})) (ref.as_non_null (local.get ${extra})))"
    if k == "repoint":
        return f"(local.set ${op[1]} (local.get ${op[2]}))"
    if k == "hstore":
        loc = op[1]
        return f"(struct.set $H {0 if loc[0] == 'a' else 1} (local.get $h) (local.get ${loc}))"
    if k == "hload":
        loc = op[1]
        return f"(local.set ${loc} (struct.get $H {0 if loc[0] == 'a' else 1} (local.get $h)))"
    if k == "rstore":
        return f"(array.set $R (local.get $r) (i32.const {op[2]}) (local.get ${op[1]}))"
    if k == "rload":
        return f"(local.set ${op[1]} (array.get $R (local.get $r) (i32.const {op[2]})))"
    if k == "new":
        _, loc, dyn, cs = op
        return f"(local.set ${loc} {_new(dyn, cs)})"
    if k == "if":
        _, bit, th, el = op
        body = "\n      ".join(emit(o) for o in th)
        ebody = "\n      ".join(emit(o) for o in el)
        return (f"(if (i32.and (i32.wrap_i64 (i64.shr_u {_ACC} (i64.const {bit}))) (i32.const 1))\n"
                f"      (then {body})\n      (else {ebody}))")
    if k == "loop":
        _, n, body = op
        b = "\n        ".join(emit(o) for o in body)
        return (f"(local.set $i (i32.const {n}))\n    (loop $l\n        {b}\n"
                f"        (local.set $i (i32.sub (local.get $i) (i32.const 1)))\n"
                f"        (br_if $l (local.get $i)))")
    raise AssertionError(k)


def _new(dyn, cs):
    if dyn in _FIELDS:
        args = []
        for w, c in zip(_FIELDS[dyn], cs):
            args.append(f"(i64.const {_sx(c, 32)})" if w == "i64" else f"(i32.const {_sx(c, 32)})")
        return f"(struct.new ${dyn} {' '.join(args)})"
    return f"(array.new_fixed ${dyn} 4 {' '.join(f'(i32.const {_sx(c, 32)})' for c in cs)})"


# ---------------------------------------------------------------- model

class _Obj:
    __slots__ = ("ty", "vals")

    def __init__(self, ty, cs):
        self.ty = ty
        if ty in _FIELDS:
            self.vals = [_sx(c, 32) & M64 if w == "i64" else _store(w, c) for w, c in zip(_FIELDS[ty], cs)]
        else:
            self.vals = [_store(_ARR[ty], c) for c in cs[:4]]


class _Model:
    def __init__(self):
        self.acc = 0
        self.loc = {}
        self.hold = [None, None]
        self.rarr = [None] * 4

    def mix(self, v):
        self.acc = (self.acc * 31 + v) & M64

    def read(self, ty_width, raw, ext):
        if ty_width == "i64":
            return raw
        if ty_width == "i32":
            return _sx(raw, 32) & M64
        bits = 8 if ty_width == "i8" else 16
        v = _sx(raw, bits) if ext == "s" else raw
        return v & M64  # i32 result then extend_i32_s: an i8/i16 value always fits

    def setval(self, w, c):
        if w == "i64":
            return _store(w, self.acc ^ (_sx(c, 32) & M64))
        return _store(w, _wrap32(self.acc) ^ _wrap32(c))

    def idx(self, idx, obj):
        return idx[1] if idx[0] == "c" else _wrap32(self.acc) % len(obj.vals)

    def run(self, op):
        k = op[0]
        L = self.loc
        if k == "sget":
            _, loc, st, f, ext = op
            o = L[loc]
            self.mix(self.read(_FIELDS[o.ty][f], o.vals[f], ext))
        elif k == "mixget":
            _, loc, st, st2, f, c = op
            o = L[loc]
            w = _FIELDS[o.ty][f]
            o.vals[f] = self.setval(w, c)
            self.mix(self.read(w, o.vals[f], "s"))
        elif k == "sset":
            _, loc, st, f, c = op
            o = L[loc]
            o.vals[f] = self.setval(_FIELDS[o.ty][f], c)
        elif k == "aget":
            _, loc, st, idx, ext = op
            o = L[loc]
            self.mix(self.read(_ARR[o.ty], o.vals[self.idx(idx, o)], ext))
        elif k == "aset":
            _, loc, st, idx, c = op
            o = L[loc]
            o.vals[self.idx(idx, o)] = self.setval(_ARR[o.ty], c)
        elif k == "alen":
            self.mix(len(L[op[1]].vals))
        elif k == "afill":
            _, loc, st, off, c, n = op
            o = L[loc]
            v = self.setval(_ARR[o.ty], c)
            for i in range(off, off + n):
                o.vals[i] = v
        elif k == "acopy":
            _, d, dt, doff, s, stt, soff, n = op
            src = L[s].vals[soff:soff + n]
            L[d].vals[doff:doff + n] = src
        elif k == "call":
            _, h, loc, extra, c = op
            o = L[loc]
            if h in ("hsetA0", "hsetP"):
                o.vals[0] = self.setval("i32", c)
            elif h == "hsetA1":
                o.vals[1] = self.setval("i64", c)
            elif h == "hgetA0":
                self.mix(self.read("i32", o.vals[0], "s"))
            elif h == "hsetV":
                o.vals[extra] = self.setval("i32", c)
            else:
                src = L[extra].vals[0:3]
                o.vals[1:4] = src
        elif k == "repoint":
            L[op[1]] = L[op[2]]
        elif k == "hstore":
            self.hold[0 if op[1][0] == "a" else 1] = L[op[1]]
        elif k == "hload":
            L[op[1]] = self.hold[0 if op[1][0] == "a" else 1]
        elif k == "rstore":
            self.rarr[op[2]] = L[op[1]]
        elif k == "rload":
            L[op[1]] = self.rarr[op[2]]
        elif k == "new":
            _, loc, dyn, cs = op
            L[loc] = _Obj(dyn, cs)
        elif k == "if":
            _, bit, th, el = op
            for o in (th if (self.acc >> bit) & 1 else el):
                self.run(o)
        elif k == "loop":
            _, n, body = op
            for _ in range(n):
                for o in body:
                    self.run(o)
        else:
            raise AssertionError(k)


def _signed64(x):
    return x - (1 << 64) if x >> 63 else x


def gcalias_gen(i, n_ops=None):
    r = random.Random(0x6CA11A5 ^ i)
    g = _Gen(r)
    m = _Model()
    init = []
    for fam, locs in _LOCALS.items():
        for loc in locs:
            if init and r.random() < 0.35:
                same = [l for l in init if l[0][0] == fam[0] and _fam_of(l[1]) == fam]
                if same:
                    src = r.choice(same)[0]
                    g.dyn[loc] = g.dyn[src]
                    init.append((loc, g.dyn[loc], None, src))
                    continue
            dyn = r.choice({"a": "ABCD", "p": ["P", "Q"], "v": ["V", "W"], "b": ["V8"]}[fam])
            g.dyn[loc] = dyn
            init.append((loc, dyn, [r.randrange(1 << 32) for _ in range(4)], None))
    g.hold = [g.dyn["a0"], g.dyn["v0"]]
    g.rarr = [g.dyn["a0"]] * 4
    ops = []
    for _ in range(n_ops or r.randrange(8, 40)):
        op = g.gen_op(True)
        g.track_store(op)
        ops.append(op)

    # model
    for loc, dyn, cs, src in init:
        m.loc[loc] = m.loc[src] if src else _Obj(dyn, cs)
    m.hold = [m.loc["a0"], m.loc["v0"]]
    m.rarr = [m.loc["a0"]] * 4
    for op in ops:
        m.run(op)
    # final sweep: fold every local's object state into the checksum
    sweep = []
    for loc in sum(_LOCALS.values(), []):
        o = m.loc[loc]
        if loc[0] in "ap":
            for f, w in enumerate(_FIELDS[o.ty]):
                sweep.append(("sget", loc, o.ty, f, "u"))
        else:
            for j in range(4):
                sweep.append(("aget", loc, o.ty, ("c", j), "u"))
    for op in sweep:
        m.run(op)

    locals_decl = " ".join(f"(local ${loc} (ref null ${_LTYPE[loc[0]]}))" for loc in sum(_LOCALS.values(), []))
    body = []
    for loc, dyn, cs, src in init:
        body.append(f"(local.set ${loc} (local.get ${src}))" if src else f"(local.set ${loc} {_new(dyn, cs)})")
    body.append("(local.set $h (struct.new $H (local.get $a0) (local.get $v0)))")
    body.append("(local.set $r (array.new $R (local.get $a0) (i32.const 4)))")
    body += [emit(op) for op in ops]
    body += [emit(op) for op in sweep]
    wat = (f"(module\n{TYPES}{HELPERS}  (func (export \"f\") (result i64) (local $acc i64) (local $i i32)\n"
           f"    {locals_decl} (local $h (ref null $H)) (local $r (ref null $R))\n    "
           + "\n    ".join(body) + "\n    (local.get $acc)))\n")
    return f"gcalias-{len(ops)}ops", "f", f"OK {_signed64(m.acc)}", wat


def _fam_of(dyn):
    return {**_FAM, **_AFAM}[dyn]
