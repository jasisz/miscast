"""The type-graph mutator: take a valid GC module and deliberately break a TYPE RELATIONSHIP
— the soundness-critical surface no other tool targets (wasm-smith stays valid, wasm-mutate stays
semantics-preserving). Each mutation aims to produce an ill-typed (or boundary) module; the runner
then asks every engine whether it rejects it (a SUT that accepts an ill-typed module is unsound).

Operations:
  op-slot       re-point one op's type slot (call_indirect / call_ref / array.* / struct.* /
                ref.test / ref.cast / br_on_cast) at every other declared + abstract type
  drop-sub      drop a declared supertype: `(sub $parent (` -> `(sub (`
  flip-sub      re-point a supertype at a different declared type: `(sub $a (` -> `(sub $b (`
  reorder-rec   swap the first two members of a `(rec ...)` group -> different iso-recursive
                identity (the canonicalization edge that broke Talos on type-rec.wast)
"""
import re

TYPE_DECL = re.compile(r"\(type\s+(\$\w+)\s*\(")
ABSTRACT_HT = ["any", "eq", "i31", "struct", "array", "none", "func", "nofunc", "extern", "noextern"]
_HT = "|".join(ABSTRACT_HT)

OPS = [
    (re.compile(r"(call_indirect\s+\(type\s+)(\$\w+)(\s*\))"),                       "call_indirect", "typeidx"),
    (re.compile(r"(call_ref\s+)(\$\w+)()"),                                          "call_ref",      "typeidx"),
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


def mutate_module(wat):
    """Return [(label, variant_wat), ...] — type-relationship breakages of `wat`."""
    out = []
    declared = list(dict.fromkeys(TYPE_DECL.findall(wat)))

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

    # (3) rec-group identity: swap the first two members of each rec group (iso-recursive edge).
    for m in re.finditer(r"\(rec\b", wat):
        end = _balanced(wat, m.start())
        inner = wat[m.start() + 4:end - 1]                      # strip "(rec" ... ")"
        kids = _children(inner)
        if len(kids) >= 2:
            (sa, ea), (sb, eb) = kids[0], kids[1]
            swapped = inner[:sa] + inner[sb:eb] + inner[ea:sb] + inner[sa:ea] + inner[eb:]
            out.append(("reorder-rec", wat[:m.start()] + "(rec" + swapped + ")" + wat[end:]))

    return out
