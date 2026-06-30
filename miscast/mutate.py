"""The type-graph mutator: take a valid GC module and deliberately break a TYPE RELATIONSHIP
— the soundness-critical surface no other tool targets (wasm-smith stays valid, wasm-mutate stays
semantics-preserving). Each mutation aims to produce an ill-typed (or boundary) module; the runner
then asks every engine whether it rejects it (a SUT that accepts an ill-typed module is unsound).

Operations:
  op-slot       re-point one op's type slot (call_indirect / call_ref / array.* / struct.* /
                ref.test / ref.cast / br_on_cast) at every other declared + abstract type
  tag-use       re-point throw / catch / catch_ref at a different declared exception tag
  drop-sub      drop a declared supertype: `(sub $parent (` -> `(sub (`
  flip-sub      re-point a supertype at a different declared type: `(sub $a (` -> `(sub $b (`
  reorder-rec   swap / rotate / reverse members of a `(rec ...)` group -> different iso-recursive
                identity (the canonicalization edge that broke Talos on type-rec.wast)
"""
import re

TYPE_DECL = re.compile(r"\(type\s+(\$\w+)\s*\(")
TAG_DECL = re.compile(r"\(tag\s+(\$\w+)")
ABSTRACT_HT = ["any", "eq", "i31", "struct", "array", "none", "func", "nofunc", "extern", "noextern"]
_HT = "|".join(ABSTRACT_HT)

OPS = [
    (re.compile(r"(call_indirect\s+\(type\s+)(\$\w+)(\s*\))"),                       "call_indirect", "typeidx"),
    (re.compile(r"(return_call_indirect\s+\(type\s+)(\$\w+)(\s*\))"),                "return_call_indirect", "typeidx"),
    (re.compile(r"(call_ref\s+)(\$\w+)()"),                                          "call_ref",      "typeidx"),
    (re.compile(r"(return_call_ref\s+)(\$\w+)()"),                                   "return_call_ref", "typeidx"),
    (re.compile(r"(array\.get(?:_[su])?\s+)(\$\w+)()"),                              "array.get",     "typeidx"),
    (re.compile(r"(array\.set\s+)(\$\w+)()"),                                        "array.set",     "typeidx"),
    (re.compile(r"(struct\.get(?:_[su])?\s+)(\$\w+)()"),                             "struct.get",    "typeidx"),
    (re.compile(r"(struct\.set\s+)(\$\w+)()"),                                       "struct.set",    "typeidx"),
    (re.compile(r"(ref\.test\s+\(ref\s+(?:null\s+)?)(\$\w+|" + _HT + r")(\s*\))"),   "ref.test",      "ref"),
    (re.compile(r"(ref\.cast\s+\(ref\s+(?:null\s+)?)(\$\w+|" + _HT + r")(\s*\))"),   "ref.cast",      "ref"),
    (re.compile(r"(br_on_cast(?:_fail)?\s+\$\w+\s+\(ref\s+(?:null\s+)?(?:\$\w+|" + _HT
                + r")\)\s+\(ref\s+(?:null\s+)?)(\$\w+|" + _HT + r")(\s*\))"),        "br_on_cast",    "ref"),
]

SUB = re.compile(r"\(sub\s+(\$\w+)\s+\(")          # a `(sub $parent (...` declaration with a supertype
TAG_USES = [
    (re.compile(r"(\bthrow\s+)(\$\w+)"), "throw-tag"),
    (re.compile(r"(\bcatch\s+)(\$\w+)"), "catch-tag"),
    (re.compile(r"(\bcatch_ref\s+)(\$\w+)"), "catch_ref-tag"),
]


def _balanced(text, idx):
    """End index (exclusive) of the balanced paren group starting at text[idx] == '('."""
    depth, instr, i = 0, False, idx
    while i < len(text):
        c = text[i]
        if instr:
            if c == "\\":
                i += 2; continue
            if c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _children(body):
    """Spans (start, end) of the top-level (...) subforms within body."""
    out, depth, start, instr, i = [], 0, None, False, 0
    while i < len(body):
        c = body[i]
        if instr:
            if c == "\\":
                i += 2; continue
            if c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "(":
            if depth == 0:
                start = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and start is not None:
                out.append((start, i + 1)); start = None
        i += 1
    return out


def _single(wat):
    """One mutation: return [(label, variant_wat), ...] — type-relationship / op breakages of `wat`."""
    out = []
    declared = list(dict.fromkeys(TYPE_DECL.findall(wat)))
    tags = list(dict.fromkeys(TAG_DECL.findall(wat)))

    # (1) op-slot sweep: re-point the earliest op slot at every candidate type.
    best = None
    for rex, op, slot in OPS:
        m = rex.search(wat)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, op, slot)
    if best is not None:
        m, op, slot = best
        cands = declared + ABSTRACT_HT if slot == "ref" else list(declared)
        for t in list(dict.fromkeys(cands)):
            out.append((f"{op}@{t.lstrip('$')}", wat[:m.start()] + m.group(1) + t + m.group(3) + wat[m.end():]))

    # (2) supertype edges: drop, and re-point at every other declared type.
    for m in SUB.finditer(wat):
        parent = m.group(1)
        out.append((f"drop-sub@{parent.lstrip('$')}", wat[:m.start()] + "(sub (" + wat[m.end():]))
        for t in declared:
            if t != parent:
                out.append((f"flip-sub@{parent.lstrip('$')}->{t.lstrip('$')}",
                            wat[:m.start()] + f"(sub {t} (" + wat[m.end():]))

    # (3) exception tag uses: re-point throw/catch/catch_ref at another declared tag. This tends to create
    #     arity / payload-type / handler-routing mistakes that validation must reject.
    if len(tags) > 1:
        for rex, label in TAG_USES:
            for m in rex.finditer(wat):
                cur = m.group(2)
                for t in tags:
                    if t != cur:
                        out.append((f"{label}@{cur.lstrip('$')}->{t.lstrip('$')}",
                                    wat[:m.start()] + m.group(1) + t + wat[m.end():]))

    # (4) rec-group identity: swap / rotate / reverse members of each rec group (iso-recursive edge).
    for m in re.finditer(r"\(rec\b", wat):
        end = _balanced(wat, m.start())
        inner = wat[m.start() + 4:end - 1]                      # strip "(rec" ... ")"
        kids = _children(inner)
        if len(kids) >= 2:
            (sa, ea), (sb, eb) = kids[0], kids[1]
            swapped = inner[:sa] + inner[sb:eb] + inner[ea:sb] + inner[sa:ea] + inner[eb:]
            out.append(("reorder-rec", wat[:m.start()] + "(rec" + swapped + ")" + wat[end:]))
        if len(kids) >= 3:
            parts = [inner[a:b] for a, b in kids]
            prefix, suffix = inner[:kids[0][0]], inner[kids[-1][1]:]
            out.append(("rotate-rec", wat[:m.start()] + "(rec" + prefix + "".join(parts[1:] + parts[:1]) + suffix + ")" + wat[end:]))
            out.append(("reverse-rec", wat[:m.start()] + "(rec" + prefix + "".join(reversed(parts)) + suffix + ")" + wat[end:]))

    # (5) nullability variance: toggle `null` in each `(ref [null] X)` — probes covariance of refs.
    for m in re.finditer(r"\(ref\s+(null\s+)?(\$\w+|" + _HT + r")\)", wat):
        has_null, ty = m.group(1), m.group(2)
        rep = f"(ref {ty})" if has_null else f"(ref null {ty})"
        out.append((("drop-null@" if has_null else "add-null@") + ty.lstrip("$"),
                    wat[:m.start()] + rep + wat[m.end():]))

    # (6) ref-swap: re-point each declared-type `(ref [null] $x)` at every other declared type.
    for m in re.finditer(r"\(ref\s+(null\s+)?(\$\w+)\)", wat):
        nullp, cur = m.group(1) or "", m.group(2)
        for t in declared:
            if t != cur:
                out.append((f"ref-swap@{cur.lstrip('$')}->{t.lstrip('$')}",
                            wat[:m.start()] + f"(ref {nullp}{t})" + wat[m.end():]))

    # (7) final-toggle: add / remove `final` on each sub type — probes the no-subtyping-of-final rule.
    for m in re.finditer(r"\(sub\s+(final\s+)?", wat):
        rep = "(sub " if m.group(1) else "(sub final "
        out.append(("drop-final" if m.group(1) else "add-final", wat[:m.start()] + rep + wat[m.end():]))

    # (8) cast ladders: wrap a struct.new / array.new in a value-preserving up-and-down ref.cast
    #     chain of growing depth. Every cast genuinely succeeds (the value IS each of those types),
    #     so the result is unchanged — but it hammers the engine's ref.cast lowering, the GC-codegen
    #     surface where mature engines actually have bugs (and that the validator can't catch).
    nm = re.search(r"\((struct\.new(?:_default)?|array\.new(?:_default|_fixed)?)\s+(\$\w+)", wat)
    if nm:
        op, ty = nm.group(1), nm.group(2)
        end = _balanced(wat, nm.start())
        expr = wat[nm.start():end]
        base = "struct" if op.startswith("struct") else "array"
        cycle = [base, "eq", "any"]                            # each a valid up/down cast — all succeed
        for reps in (1, 2, 4, 8, 16):                          # depth up to 48 casts — hammer the lowering
            wrapped = expr
            for ht in cycle * reps:
                wrapped = f"(ref.cast (ref {ht}) {wrapped})"
            wrapped = f"(ref.cast (ref {ty}) {wrapped})"        # land back on the concrete type
            out.append((f"cast-ladder@{ty.lstrip('$')}:{3 * reps}", wat[:nm.start()] + wrapped + wat[end:]))

        # ref through nested control flow: a different codegen path than casts (block params / ref
        # values flowing through control flow), value- and identity-preserving.
        for n in (1, 4, 16):
            wrapped = expr
            for _ in range(n):
                wrapped = f"(block (result (ref {ty})) {wrapped})"
            out.append((f"block-roundtrip@{ty.lstrip('$')}:{n}", wat[:nm.start()] + wrapped + wat[end:]))

        # ref-identity roundtrips through GC STORAGE (struct field / array element) — the semantic
        # surface where mature engines actually break (wasmtime's array.init_elem reference-identity
        # bug lived here). Inject wrapper types holding (ref null any), store the ref and read it back:
        # value AND identity must survive, so the result is deterministic and the differential catches
        # any divergence in the engine's GC load/store codegen.
        if "$__ws" not in wat:
            mend = re.search(r"\(module(\s+\$\S+)?", wat).end()
            wrap = (" (type $__ws (struct (field (mut (ref null any)))))"
                    " (type $__wa (array (mut (ref null any))))")

            def inject(new_expr):
                b = wat[:nm.start()] + new_expr + wat[end:]
                return b[:mend] + wrap + b[mend:]

            sg = f"(struct.get $__ws 0 (struct.new $__ws {expr}))"
            ag = f"(array.get $__wa (array.new_fixed $__wa 1 {expr}) (i32.const 0))"
            out.append((f"ref-id-struct@{ty.lstrip('$')}", inject(f"(ref.cast (ref {ty}) {sg})")))
            out.append((f"ref-id-array@{ty.lstrip('$')}", inject(f"(ref.cast (ref {ty}) {ag})")))
            for n in (3, 8):                                    # deep storage roundtrips
                e = expr
                for _ in range(n):
                    e = f"(struct.get $__ws 0 (struct.new $__ws {e}))"
                out.append((f"ref-id-deep@{ty.lstrip('$')}:{n}", inject(f"(ref.cast (ref {ty}) {e})")))

    return out


def mutate_module(wat, compound=3, branch=4):
    """Single-op mutations PLUS compound variants that stack several mutations at once — a deep
    pile-up that no single corpus case (or semantics-preserving tool) produces. Bounded by `branch`
    per level over `compound` levels so the count stays runnable."""
    out = _single(wat)
    frontier = out[:branch]
    for _ in range(compound):                                  # stack up to `compound` more mutations
        nxt = []
        for label, vm in frontier:
            for lbl2, v2 in _single(vm)[:branch]:
                out.append((f"{label}+{lbl2}", v2))
                nxt.append((f"{label}+{lbl2}", v2))
        frontier = nxt[:branch]
    return out
