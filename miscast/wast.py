"""Parse a .wast script into runnable cases.

A case is a 5-tuple: (name, module_wat, export, args, expected), where args is a
list of (type, value) and expected is "TRAP" / "OK <bits>" / "RET" (non-trap,
value uncaptured) / None (a bare invoke). assert_invalid / assert_malformed are
counted but not run (validation-differential is not yet implemented). A bare .wat
file becomes a single case exercised by (invoke "f").
"""
import glob
import os
import re

from .toolchain import u32

ARG_RE = re.compile(r"\((i32|i64)\.const\s+(-?(?:0x[0-9a-fA-F]+|\d+))\)")
I32RES_RE = re.compile(r"\(i32\.const\s+(-?(?:0x[0-9a-fA-F]+|\d+))\)")
_SIG = r"\s*((?:\((?:param|result)[^()]*\)\s*)*)"


def _sig_results(module, export):
    m = re.search(r'\(func\s+\(export\s+"%s"\)%s' % (re.escape(export), _SIG), module)
    if not m:
        em = re.search(r'\(export\s+"%s"\s+\(func\s+(\$\w+)\)' % re.escape(export), module)
        if em:
            m = re.search(r"\(func\s+%s\b%s" % (re.escape(em.group(1)), _SIG), module)
    if not m:
        return None
    r = re.search(r"\(result\s+([^()]*)\)", m.group(1))
    return r.group(1).split() if r else []


def result_type(module, export):
    """How to compare the export's result value across engines:
      "int"   -> all results i32/i64        (compare as u32)
      "float" -> any result f32/f64         (parse to a float, NaN canonical)
      None    -> ref / v128 / void / unknown -> compare by status (trap/return) only
    Best-effort from the module text."""
    res = _sig_results(module, export)
    if not res:
        return None
    if all(t in ("i32", "i64") for t in res):
        return "int"
    if any(t in ("f32", "f64") for t in res):
        return "float"
    return None


def strip_comments(t):
    t = re.sub(r"\(;.*?;\)", "", t, flags=re.S)
    return re.sub(r";;[^\n]*", "", t)


def top_forms(text):
    """Yield each top-level (...) s-expression, string- and comment-aware."""
    forms, depth, start, instr, i = [], 0, None, False, 0
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
            if depth == 0:
                start = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and start is not None:
                forms.append(text[start:i + 1]); start = None
        i += 1
    return forms


def find_subform(text, head):
    """Return the balanced `(head ...)` substring, or None."""
    idx = text.find("(" + head)
    if idx < 0:
        return None
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
                return text[idx:i + 1]
        i += 1
    return None


def parse_invoke(invsub):
    if re.search(r"\(invoke\s+\$", invsub):            # (invoke $module "fn") -> multi-module, skip
        return None
    mn = re.search(r'\(invoke\s+"((?:[^"\\]|\\.)*)"', invsub)
    if not mn:
        return None
    after = invsub[mn.end():]
    if re.search(r"\((?:ref|f32|f64|v128)", after):    # non-numeric args can't be passed via CLI
        return None
    return mn.group(1), [(t, v) for t, v in ARG_RE.findall(after)]


def parse_wast(text, prefix):
    text = strip_comments(text)
    cases, stats, cur, idx = [], {"invalid": 0, "skip": 0, "modules": 0}, None, 0
    for form in top_forms(text):
        if form.startswith("(module"):
            if form.startswith(("(module binary", "(module quote")):
                cur = None; stats["skip"] += 1
            else:
                cur = form; stats["modules"] += 1
        elif form.startswith(("(assert_invalid", "(assert_malformed")):
            stats["invalid"] += 1
        elif form.startswith(("(assert_return", "(assert_trap")):
            inv = find_subform(form, "invoke")
            pi = parse_invoke(inv) if inv else None
            if cur is None or pi is None:
                stats["skip"] += 1; continue
            name, args = pi
            if form.startswith("(assert_trap"):
                expected = "TRAP"
            else:
                mres = I32RES_RE.search(form[form.find(inv) + len(inv):])
                expected = ("OK " + u32(mres.group(1))) if mres else "RET"
            idx += 1
            cases.append((f"{prefix}:{name}#{idx}", cur, name, args, expected, result_type(cur, name)))
        elif form.startswith("(invoke"):
            pi = parse_invoke(form)
            if cur is None or pi is None:
                stats["skip"] += 1; continue
            name, args = pi; idx += 1
            cases.append((f"{prefix}:{name}#{idx}", cur, name, args, None, result_type(cur, name)))
    return cases, stats


def load_corpus(d):
    """Load every .wast / .wat file in `d` into a flat list of cases + aggregate stats."""
    cases, stats = [], {"files": 0, "invalid": 0, "skip": 0, "modules": 0}
    for f in sorted(glob.glob(os.path.join(d, "*.wast"))) + sorted(glob.glob(os.path.join(d, "*.wat"))):
        stats["files"] += 1
        txt, base = open(f).read(), os.path.splitext(os.path.basename(f))[0]
        if f.endswith(".wast"):
            cs, st = parse_wast(txt, base)
            cases += cs
            for k in ("invalid", "skip", "modules"):
                stats[k] += st[k]
        else:
            cases.append((f"{base}:f", txt, "f", [], None, result_type(txt, "f"))); stats["modules"] += 1
    return cases, stats
