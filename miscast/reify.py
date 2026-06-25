"""Reify a GC-reference result into an i32 fingerprint so the value-differential can SEE it.

A function whose result is a reference returns an opaque pointer that every engine prints differently
(or as the constant "ref"), so miscast can otherwise compare it only by status (trap vs return) — blind
to the exact bug it hunts: a subtype/cast unsoundness that returns a WRONG-TYPED object instead of
trapping. This rewrites such a function to return an i32 fingerprint of the reference's ABSTRACT type
membership (ref.is_null + ref.test against i31/struct/array/eq + the i31 payload), built from probes
valid for ANY any-rooted GC reference without knowing its concrete declared type. A misclassifying
engine then produces a different fingerprint -> a VALUE divergence.

Safety: the reified module is run by EVERY engine, so a quirk in the rewrite affects them identically
and can never manufacture a divergence; a malformed rewrite simply fails to assemble and the case is
dropped. Only any-rooted (GC) references are handled; func/extern references are left untouched.
"""
import re

_HEADER_HEADS = ("export", "param", "result", "type", "local")
_R = "(local.get $__rf)"
# i32 fingerprint of the captured reference: bit0 null, bit1 i31, bit2 struct, bit3 array, bit4 eq,
# bits 8-15 the i31 payload (guarded by a non-null i31 test so the cast never traps).
_FINGERPRINT = (
    "(i32.or (ref.is_null {r})"
    " (i32.or (i32.shl (ref.test (ref i31) {r}) (i32.const 1))"
    " (i32.or (i32.shl (ref.test (ref struct) {r}) (i32.const 2))"
    " (i32.or (i32.shl (ref.test (ref array) {r}) (i32.const 3))"
    " (i32.or (i32.shl (ref.test (ref eq) {r}) (i32.const 4))"
    " (i32.shl (if (result i32) (ref.test (ref i31) {r})"
    " (then (i32.and (i31.get_u (ref.cast (ref i31) {r})) (i32.const 255)))"
    " (else (i32.const 0))) (i32.const 8)))))))"
).format(r=_R)


def _balanced(text, idx):
    """End index (exclusive) of the balanced paren group whose '(' is at text[idx]."""
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


def _find_func(module, export):
    """(start, end) of the (func ...) form the export resolves to, or None."""
    m = re.search(r'\(func\s+\(export\s+"%s"\)' % re.escape(export), module)
    if m:
        return m.start(), _balanced(module, m.start())
    em = re.search(r'\(export\s+"%s"\s+\(func\s+(\$\w+)\)' % re.escape(export), module)
    if em:
        fid = re.escape(em.group(1))
        for fm in re.finditer(r"\(func\s+%s\b" % fid, module):
            if not module[fm.end():].lstrip().startswith(")"):   # the DEFINITION, not the export ref
                return fm.start(), _balanced(module, fm.start())
    return None


def _tokens(form):
    """(head, [child-token, ...]) for a balanced (head ...) form; children are atoms or balanced subforms."""
    i = 1
    while i < len(form) and form[i] not in " \t\n()":
        i += 1
    head, toks, i = form[1:i], [], i
    while i < len(form) - 1:
        c = form[i]
        if c in " \t\n":
            i += 1; continue
        if c == ")":
            break
        if c == "(":
            j = _balanced(form, i); toks.append(form[i:j]); i = j
        else:
            j = i
            while j < len(form) and form[j] not in " \t\n()":
                j += 1
            toks.append(form[i:j]); i = j
    return head, toks


def _head(tok):
    return tok[1:].lstrip().split(None, 1)[0].rstrip(")") if tok.startswith("(") else None


def _single_ref_result(rtok):
    """If (result ...) is exactly one (ref ...), return its heap type (e.g. 'struct', '$node'); else None."""
    inner = rtok[len("(result"):].strip()
    inner = inner[:-1].strip() if inner.endswith(")") else inner   # drop the (result ...) close paren
    if not inner.startswith("(ref") or _balanced(inner, 0) != len(inner):
        return None                                                 # not a ref, or more than one result
    ht = inner[len("(ref"):-1].strip()
    return ht.replace("null", "").strip()


def _is_anyrooted(ht, module):
    """Is heap type `ht` rooted at `any` (a GC value storable in (ref null any) and probe-able)?"""
    if ht in ("any", "eq", "struct", "array", "i31", "none"):
        return True
    if ht in ("func", "nofunc", "extern", "noextern", "exn", "noexn"):
        return False
    if ht.startswith("$"):
        dm = re.search(r"\(type\s+%s\b" % re.escape(ht), module)
        if not dm:
            return False
        seg = module[dm.start():_balanced(module, dm.start())]
        km = re.search(r"\((struct|array|func)\b", seg)   # first composite (works through a (sub ...) wrapper)
        return bool(km) and km.group(1) in ("struct", "array")
    return False


def reify_ref_result(module, export):
    """If `export` returns a single any-rooted GC reference, return (reified_module, True) where the
    function instead returns an i32 fingerprint of that reference; otherwise (module, False)."""
    loc = _find_func(module, export)
    if not loc:
        return module, False
    s, e = loc
    head, toks = _tokens(module[s:e])
    if head != "func":
        return module, False
    header, body, ht, found, in_header = [], [], None, False, True
    for k, tok in enumerate(toks):
        if in_header:
            is_name = (k == 0 and not tok.startswith("(") and tok.startswith("$"))
            h = _head(tok)
            if is_name or h in _HEADER_HEADS:
                if h == "result":
                    rt = _single_ref_result(tok)
                    if rt is None or found:
                        return module, False              # not a lone ref result
                    ht, found = rt, True
                    header.append("(result i32)")
                    continue
                header.append(tok)
                continue
            in_header = False
        body.append(tok)
    if not found or not body or not _is_anyrooted(ht, module):
        return module, False
    if any(re.search(r"\breturn\b", t) for t in body):
        return module, False                              # explicit return would mismatch the new i32 result
    header.append("(local $__rf (ref null any))")
    new_func = "(func " + " ".join(header + body) + " local.set $__rf " + _FINGERPRINT + ")"
    return module[:s] + new_func + module[e:], True


def bitcast_float_result(module, export):
    """If `export` returns a single f32/f64, rewrite it to return the reinterpreted integer bits so the
    value-differential compares BIT-EXACTLY — the 6-decimal text compare in verdict._fkey misses sub-ULP
    miscompiles (a JIT codegen bug that is off by one ULP prints identically). Returns (module, 'f32'|'f64')
    naming the source width, or (module, None). NaN canonicalization happens at the comparison key (a NaN
    bit-pattern has a spec-nondeterministic payload), so this never manufactures a divergence on NaN."""
    loc = _find_func(module, export)
    if not loc:
        return module, None
    s, e = loc
    head, toks = _tokens(module[s:e])
    if head != "func":
        return module, None
    header, body, width, found, in_header = [], [], None, False, True
    for k, tok in enumerate(toks):
        if in_header:
            is_name = (k == 0 and not tok.startswith("(") and tok.startswith("$"))
            h = _head(tok)
            if is_name or h in _HEADER_HEADS:
                if h == "result":
                    inner = tok[len("(result"):].strip()
                    inner = inner[:-1].strip() if inner.endswith(")") else inner
                    if found or inner not in ("f32", "f64"):
                        return module, None                # multiple results, or not a lone float
                    width, found = inner, True
                    header.append("(result i32)" if inner == "f32" else "(result i64)")
                    continue
                header.append(tok)
                continue
            in_header = False
        body.append(tok)
    if not found or not body or any(re.search(r"\breturn\b", t) for t in body):
        return module, None
    op = "i32.reinterpret_f32" if width == "f32" else "i64.reinterpret_f64"
    new_func = "(func " + " ".join(header + body) + " " + op + ")"
    return module[:s] + new_func + module[e:], width
