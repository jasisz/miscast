"""The subtyping sweep: re-point one type slot in a module's text at every
declared type (and every abstract heap type for `(ref X)` slots), deliberately
constructing the ill-typed cases a soundness bug needs.
"""
import re

TYPE_DECL = re.compile(r"\(type\s+(\$\w+)\s*\(")
ABSTRACT_HT = ["any", "eq", "i31", "struct", "array", "none", "func", "nofunc", "extern", "noextern"]
_HT = "|".join(ABSTRACT_HT)

# Each op: (regex with 3 groups — prefix, type-token, suffix), name, slot-kind.
#   typeidx : a defined-type index -> sweep declared $-types only
#   ref     : a `(ref X)` slot      -> sweep declared $-types AND abstract heap types
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


def mutate_module(wat):
    """Construct ill-typed variants of a module two ways:
      (1) re-point the earliest op-slot at every candidate type (sweep the relationship matrix);
      (2) break the subtype graph — drop each declared supertype `(sub $parent (` -> `(sub (`,
          so a cast / indirect call that relied on that relationship becomes ill-typed.
    Returns [(label, variant_wat), ...]."""
    out = []
    declared = list(dict.fromkeys(TYPE_DECL.findall(wat)))
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
    for m in SUB.finditer(wat):                    # break each declared supertype relationship
        out.append((f"drop-sub@{m.group(1).lstrip('$')}", wat[:m.start()] + "(sub (" + wat[m.end():]))
    return out
