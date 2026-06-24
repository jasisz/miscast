"""Parse a .wast script into runnable segments / cases.

`parse_script` (the current path) turns a file into an ordered list of SEGMENTS — a module plus
the actions that apply to it (kind "run"), or an `assert_invalid` module that a conformant engine
must reject (kind "invalid"). `parse_wast` (the legacy flat path, still used by mutate / smith)
yields per-invoke cases. A bare .wat file becomes one module exercised by (invoke "f").
assert_malformed and multi-module `register` are not modeled yet (counted under malformed / skip).
"""
import glob
import os
import re

from .toolchain import u32

ARG_RE = re.compile(r"\((i32|i64)\.const\s+(-?(?:0x[0-9a-fA-F]+|\d+))\)")
I32RES_RE = re.compile(r"\(i32\.const\s+(-?(?:0x[0-9a-fA-F]+|\d+))\)")
_SIG = r"\s*((?:\((?:param|result)[^()]*\)\s*)*)"

# Persistent-state mutators (NOT local.*, which is function-local): a run-segment with more than
# one action and any of these may carry state from one invoke to the next, so a one-shot engine
# (fresh instance per call) can't replicate it faithfully.
MUTATOR = re.compile(r"\b(?:global\.set|table\.(?:set|fill|copy|init|grow)|elem\.drop|data\.drop|"
                     r"memory\.(?:fill|copy|init|grow)|(?:i32|i64|f32|f64|v128)\.store\w*)\b")


def _assert_reason(form):
    """The trailing expected-failure string of an assert_invalid / assert_malformed form."""
    m = re.findall(r'"((?:[^"\\]|\\.)*)"', form)
    return m[-1] if m else ""


def _sig_results(module, export):
    """Result value-types of `export`'s function, or None if not found / not a function export."""
    m = re.search(r'\(func\s+\(export\s+"%s"\)%s' % (re.escape(export), _SIG), module)
    if not m:
        em = re.search(r'\(export\s+"%s"\s+\(func\s+(\$\w+)\)' % re.escape(export), module)
        if em:
            fid = re.escape(em.group(1))
            # take the func DEFINITION, not the `(export "x" (func $id))` reference (which is
            # `(func $id)` immediately closed) — else, when the export precedes the definition,
            # we'd read an empty signature and silently disable value comparison.
            for fm in re.finditer(r"\(func\s+%s\b" % fid, module):
                if not module[fm.end():].lstrip().startswith(")"):
                    m = re.match(_SIG, module[fm.end():])
                    break
    if not m:
        return None
    r = re.search(r"\(result\s+([^()]*)\)", m.group(1))
    return r.group(1).split() if r else []


def result_type(module, export):
    """How to compare the export's result value across engines:
      "int"   -> all results i32            (compare as u32)
      "int64" -> any result i64             (compare as u64 — a 32-bit mask hides a high-word diff)
      "float" -> any result f32/f64         (parse to a float, NaN canonical)
      None    -> ref / v128 / void / unknown -> compare by status (trap/return) only
    Best-effort from the module text."""
    res = _sig_results(module, export)
    if not res:
        return None
    if all(t == "i32" for t in res):
        return "int"
    if all(t in ("i32", "i64") for t in res):
        return "int64"
    if any(t in ("f32", "f64") for t in res):
        return "float"
    return None


def strip_comments(t):
    t = re.sub(r"\(;.*?;\)", "", t, flags=re.S)
    return re.sub(r";;[^\n]*", "", t)


def top_forms_pos(text):
    """Yield (form, start_offset) for each top-level (...) s-expression, string- and comment-aware."""
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
                forms.append((text[start:i + 1], start)); start = None
        i += 1
    return forms


def top_forms(text):
    """Yield each top-level (...) s-expression, string- and comment-aware."""
    return [f for f, _ in top_forms_pos(text)]


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


def _norm_arg(t, v):
    """Canonicalize an i32/i64 literal to a SIGNED DECIMAL string. The spec writes operands in hex
    (`0x80000000`), but an arbitrary SUT's CLI may parse hex as 0 (wasmedge does) — and the tool's
    whole pitch is 'any interpreter, no code change', so normalize to a form every runtime accepts."""
    n = int(v, 0)                                      # parse hex / decimal / negative
    bits = 64 if t == "i64" else 32
    n &= (1 << bits) - 1                               # wrap to the value's bit-width...
    if n >= 1 << (bits - 1):                           # ...then to its signed interpretation
        n -= 1 << bits
    return str(n)


def parse_invoke(invsub):
    if re.search(r"\(invoke\s+\$", invsub):            # (invoke $module "fn") -> multi-module, skip
        return None
    mn = re.search(r'\(invoke\s+"((?:[^"\\]|\\.)*)"', invsub)
    if not mn:
        return None
    after = invsub[mn.end():]
    if re.search(r"\((?:ref|f32|f64|v128)", after):    # non-numeric args can't be passed via CLI
        return None
    return mn.group(1), [(t, _norm_arg(t, v)) for t, v in ARG_RE.findall(after)]


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


def parse_script(text, prefix):
    """Parse a .wast into an ordered list of SEGMENTS — the faithful command stream.

    A segment is a dict:
      name      : "<prefix>#k" identifier
      module    : the (module ...) wat text
      kind      : "run"     — instantiate, then run `actions` in order on ONE instance (state persists)
                  "invalid" — an ill-typed module a conformant engine must REJECT at validation
      reason    : expected-failure substring (kind=invalid)
      stateful  : True if actions may depend on state a prior action set up (one-shot engines can't replicate)
      actions   : ordered [{export, args, expected, rtype}], empty for kind=invalid
    Multi-module linking (register / (invoke $m ...)), binary/quote modules and assert_malformed
    are not modeled yet and counted under skip/malformed."""
    text = strip_comments(text)
    segs, stats, seg, k = [], {"modules": 0, "invalid": 0, "malformed": 0, "skip": 0}, None, 0

    def flush():
        nonlocal seg
        if seg is not None:
            segs.append(seg)
            seg = None

    for form, _ in top_forms_pos(text):
        if form.startswith("(module"):
            flush()
            if form.startswith(("(module binary", "(module quote")):
                stats["skip"] += 1
                continue
            k += 1
            stats["modules"] += 1
            seg = {"name": f"{prefix}#{k}", "module": form, "kind": "run",
                   "reason": "", "stateful": False, "actions": []}
        elif form.startswith("(assert_invalid"):
            flush()
            inner = find_subform(form, "module")
            if inner is None or inner.startswith(("(module binary", "(module quote")):
                stats["skip"] += 1
                continue
            k += 1
            stats["invalid"] += 1
            segs.append({"name": f"{prefix}:invalid#{k}", "module": inner, "kind": "invalid",
                         "reason": _assert_reason(form), "stateful": False, "actions": []})
        elif form.startswith("(assert_malformed"):
            stats["malformed"] += 1                       # parser-level, not modeled yet
        elif form.startswith("(register"):
            stats["skip"] += 1                            # multi-module linking not modeled
        elif form.startswith(("(assert_return", "(assert_trap")):
            inv = find_subform(form, "invoke")
            pi = parse_invoke(inv) if inv else None
            if seg is None or pi is None:
                stats["skip"] += 1
                continue
            name, args = pi
            if form.startswith("(assert_trap"):
                expected = "TRAP"
            else:
                mres = I32RES_RE.search(form[form.find(inv) + len(inv):])
                expected = ("OK " + u32(mres.group(1))) if mres else "RET"
            seg["actions"].append({"export": name, "args": args, "expected": expected,
                                   "rtype": result_type(seg["module"], name), "raw": form})
        elif form.startswith("(invoke"):
            pi = parse_invoke(form)
            if seg is None or pi is None:
                stats["skip"] += 1
                continue
            name, args = pi
            seg["actions"].append({"export": name, "args": args, "expected": None,
                                   "rtype": result_type(seg["module"], name), "raw": form})
    flush()
    for s in segs:
        if s["kind"] == "run" and len(s["actions"]) > 1 and MUTATOR.search(s["module"]):
            s["stateful"] = True
    return segs, stats


def load_script_corpus(d):
    """Load every .wast / .wat in `d` as an ordered segment list + aggregate stats."""
    segs, stats = [], {"files": 0, "modules": 0, "invalid": 0, "malformed": 0, "skip": 0,
                       "actions": 0, "stateful": 0}
    for f in sorted(glob.glob(os.path.join(d, "*.wast"))) + sorted(glob.glob(os.path.join(d, "*.wat"))):
        stats["files"] += 1
        txt, base = open(f).read(), os.path.splitext(os.path.basename(f))[0]
        if f.endswith(".wast"):
            ss, st = parse_script(txt, base)
            for s in ss:
                s["src"] = os.path.abspath(f)         # conformance runs `wasm <src>` regardless of cwd
            segs += ss
            for key in ("modules", "invalid", "malformed", "skip"):
                stats[key] += st[key]
        else:
            segs.append({"name": base, "src": f, "module": txt, "kind": "run", "reason": "",
                         "stateful": False, "actions": [{"export": "f", "args": [], "expected": None,
                                                         "rtype": result_type(txt, "f"), "raw": ""}]})
            stats["modules"] += 1
    for s in segs:
        stats["actions"] += len(s["actions"])
        stats["stateful"] += bool(s["stateful"])
    return segs, stats


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
