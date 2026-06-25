"""Engine backends.

Each backend invokes an export with args and returns a normalized verdict string:
  "OK <bits>" | "OK _" (void) | "OK ref" | "TRAP" | "UNSUP" | "NOEXPORT" | "ERR".
Engines are auto-detected; `--sut` picks the one under test, the rest are oracles.
"""
import os
import re
import shlex
import shutil
import tempfile

from .config import NODE, ORACLE, WORK, SPEC_WASM
from .toolchain import _run

_NUM = re.compile(r"-?(?:0x[0-9a-fA-F]+|\d+\.\d+(?:[eE][+-]?\d+)?|\d+(?:[eE][+-]?\d+)?|inf|nan)", re.I)
#   an integer (incl. 0x hex) or float (incl. scientific) value at the start of a custom SUT's output


def be_v8(wat, wasm, export, args):
    av = [v + ("n" if t == "i64" else "") for t, v in args]
    o = _run([NODE, ORACLE, wasm, export] + av).stdout
    if "=> OK" in o:
        return "OK " + o.split("=> OK ")[1].strip()      # raw value; classify normalizes per type
    if "TRAP" in o:
        return "TRAP"
    if "NOEXPORT" in o:
        return "NOEXPORT"
    if "INSTANTIATE-FAIL" in o:
        return "UNSUP"
    return "ERR"


def be_wasmtime(wat, wasm, export, args):
    r = _run(["wasmtime", "run", "--invoke", export, "-W", "function-references=y,gc=y", wasm] + [v for _, v in args])
    out, both = r.stdout.strip(), (r.stdout + r.stderr).lower()
    if "trap" in both:
        return "TRAP"
    if "failed to find" in both or "no func" in both or "no export" in both:
        return "NOEXPORT"
    if r.returncode != 0:
        return "UNSUP"
    return "OK " + out if out else "OK _"


MC_RUNNER = os.environ.get("MC_RUNNER") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runner", "target", "release", "mc-runner")


def be_mc_runner(wat, wasm, export, args):
    """The mc-runner embedder (wasmtime crate): EXACT result bits, and a gc-capable wasmtime even when the
    PATH `wasmtime` was built without the gc feature (Homebrew's is). Parses its JSON into a verdict."""
    import json
    av = []
    for t, v in args:
        if t in ("i32", "i64"):                          # CLI can't pass float/ref args (parse_invoke skips them)
            av += ["--arg", f"{t}:{v}"]
    r = _run([MC_RUNNER, wasm, "--invoke", export] + av)
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    try:
        j = json.loads(line)
    except ValueError:
        return "ERR"
    if not j.get("ok"):
        return "TRAP" if j.get("trap") else "UNSUP"
    res = j.get("results", [])
    if len(res) != 1:
        return "OK _"                                    # void or multi-value -> status only
    kind, _, val = res[0].partition(":")
    if kind in ("i32", "i64"):
        return "OK " + val
    if kind in ("f32", "f64"):
        return "OK _"                                    # raw float bits would clash with wasmtime's text print
    return "OK ref" if kind.startswith("ref") else "OK _"


def wasmtime_has_gc():
    """Whether the PATH `wasmtime` was built with the gc cargo feature. A build without it (Homebrew's)
    silently rejects every GC module, so wasmtime contributes nothing as a GC oracle without saying so."""
    if not shutil.which("wasmtime"):
        return False
    r = _run(["wasmtime", "run", "-W", "gc=y", os.devnull])
    both = (r.stdout + r.stderr).lower()
    return not ("unknown" in both and "gc" in both)      # "unknown -W / --wasm option: gc" => no gc feature


def make_be_cmd(tmpl):
    """A backend for any interpreter, driven by a command template with {wat}/{wasm}/{export}. Invoke
    arguments are appended positionally; set `CUSTOM_NO_ARGS=1` for a SUT whose runner cannot take per-call
    arguments (e.g. an `--invoke` that silently ignores them) so arg-taking actions are skipped (`SUT_NA`)
    instead of comparing a default-argument run against the oracles and mis-reporting a value divergence."""
    no_args = bool(os.environ.get("CUSTOM_NO_ARGS"))

    def be(wat, wasm, export, args):
        if args and no_args:
            return "SUT_NA"
        r = _run(shlex.split(tmpl.format(wat=wat, wasm=wasm, export=export)) + [v for _, v in args])
        both = (r.stdout + r.stderr).lower()
        if "trap" in both:
            return "TRAP"
        m = _NUM.match(r.stdout.strip())
        if m:
            return "OK " + m.group(0)
        return "OK _" if r.returncode == 0 and not r.stdout.strip() else "UNSUP"
    return be


def be_spec(wat, wasm, export, args):
    """The WebAssembly reference interpreter (gold-standard oracle): build a .wast — the module
    plus one (invoke ...) — and run it, parsing its `<value> : [<type>]` output (with the digit
    separators stripped). The module and invoke must share a script, hence the temp file."""
    inv = f'(invoke "{export}"' + "".join(f" ({t}.const {v})" for t, v in args) + ")"
    fd, path = tempfile.mkstemp(suffix=".wast", dir=WORK)
    with os.fdopen(fd, "w") as o, open(wat) as src:
        o.write(src.read() + "\n" + inv + "\n")
    try:
        r = _run([SPEC_WASM, path])
    finally:
        os.unlink(path)
    if "trap" in (r.stdout + r.stderr).lower():
        return "TRAP"
    m = re.search(r"(\S[^:\n]*?)\s*:\s*\[", r.stdout)
    if m:
        return "OK " + m.group(1).replace("_", "")
    return "OK _" if r.returncode == 0 else "UNSUP"


def repro_command(engine, wat_path, wasm_path, export, args, wast_path=None):
    """The exact shell command a given engine ran for this case — so a divergence is reproducible.
    Mirrors the backends above; `wast_path` is the module+invoke script the `spec` engine needs."""
    a = [v for _, v in args]
    if engine == "v8":
        av = [v + ("n" if t == "i64" else "") for t, v in args]
        return " ".join([NODE or "node", ORACLE, wasm_path, export] + av)
    if engine == "wasmtime":
        return " ".join(["wasmtime", "run", "--invoke", export,
                         "-W", "function-references=y,gc=y", wasm_path] + a)
    if engine == "mcr":
        return " ".join([MC_RUNNER, wasm_path, "--invoke", export]
                        + sum([["--arg", f"{t}:{v}"] for t, v in args if t in ("i32", "i64")], []))
    if engine == "spec":
        return f"{SPEC_WASM or '$SPEC_WASM'} {wast_path or '<module+invoke>.wast'}"
    if engine == "custom":
        tmpl = os.environ.get("CUSTOM_CMD", "$CUSTOM_CMD")
        return tmpl.format(wat=wat_path, wasm=wasm_path, export=export) + (" " + " ".join(a) if a else "")
    return "(unknown engine)"


_VALERR = re.compile(r"valid|type mismatch|ill[- ]?typed|mismatch|expected|unexpected|"
                     r"non-defaultable|undeclared|unknown type|out of bounds|constant|"
                     r"super[- ]?type|sub[- ]?type|final\b|illegal|defaultable|arity|hierarchy", re.I)
_EXPORT_RE = re.compile(r'\(export\s+"([^"]+)"\s+\(func\b')
_FUNCEXPORT_RE = re.compile(r'\(func\s+\(export\s+"([^"]+)"')


def first_export(wat):
    m = _EXPORT_RE.search(wat) or _FUNCEXPORT_RE.search(wat)
    return m.group(1) if m else None


def _spec_validate(module_wat):
    fd, path = tempfile.mkstemp(suffix=".wast", dir=WORK)
    with os.fdopen(fd, "w") as o:
        o.write(module_wat + "\n")
    try:
        r = _run([SPEC_WASM, path])
    finally:
        os.unlink(path)
    if r.returncode == 0:
        return "ACCEPT"
    return "REJECT" if _VALERR.search(r.stdout + r.stderr) else "UNSUP"


def _v8_validate(wsm):
    if wsm is None:
        return "UNSUP"
    o = _run([NODE, ORACLE, wsm, "__validate__"]).stdout
    if "=> VALID" in o:
        return "ACCEPT"
    if "=> INVALID" in o:
        return "REJECT"
    return "UNSUP"


def _custom_validate(module_wat, wp, wsm):
    vtmpl = os.environ.get("CUSTOM_VALIDATE_CMD")
    if vtmpl:                                 # the SUT exposes a real validate/load step -> use it directly
        r = _run(shlex.split(vtmpl.format(wat=wp, wasm=wsm or "")))
        return "ACCEPT" if r.returncode == 0 else "REJECT"
    ex = first_export(module_wat)
    if ex is None:
        return "UNSUP"                        # no export to drive a one-shot runner; oracles still validate it
    tmpl = os.environ.get("CUSTOM_CMD", "")
    r = _run(shlex.split(tmpl.format(wat=wp, wasm=wsm or "", export=ex)))
    if _NUM.match(r.stdout.strip()):
        return "ACCEPT"                       # the SUT RAN an ill-typed module -> unsound
    both = r.stdout + r.stderr
    if "trap" in both.lower() or _VALERR.search(both):
        return "REJECT"
    return "UNSUP"


def validate_module(module_wat, engines):
    """Validation-differential primitive: does each engine REJECT this (allegedly invalid) module?
    Returns ({engine: 'REJECT'|'ACCEPT'|'UNSUP'} incl. 'wtools', wat_path, wasm_path)."""
    from .toolchain import prepare
    wp, wsm, valid = prepare(module_wat)
    out = {"wtools": "ACCEPT" if valid else "REJECT"}
    for en in engines:
        if en == "spec" and SPEC_WASM:
            out[en] = _spec_validate(module_wat)
        elif en == "wasmtime":
            out[en] = ("UNSUP" if wsm is None else
                       ("ACCEPT" if _run(["wasmtime", "compile", "-W", "function-references=y,gc=y",
                                          wsm, "-o", os.devnull]).returncode == 0 else "REJECT"))
        elif en == "v8" and NODE:
            out[en] = _v8_validate(wsm)
        elif en == "custom":
            out[en] = _custom_validate(module_wat, wp, wsm)
    return out, wp, wsm


def _native_wast(cmd):
    r = _run(cmd)
    if r.returncode == 0:
        return "PASS"
    both = (r.stdout + r.stderr).lower()
    if "unsupported" in both or "not supported" in both or "unknown operator" in both:
        return "UNSUP"
    return "FAIL"


def conformance_file(src_path, engines):
    """Stateful .wast conformance: run the WHOLE official .wast file verbatim, natively and in
    order (state preserved). No reconstruction — the real file is the faithful script.
    Returns {engine: PASS|FAIL|UNSUP|n/a}.
      spec      `wasm file.wast` — the reference interpreter, the conformance authority (it passes
                its own testsuite by construction, so it is the lone oracle here)
      custom    `CUSTOM_WAST_CMD="cmd {wast}"` if the SUT can run a .wast script, else n/a
      wasmtime  its `wast` runner string-matches `assert_invalid` reason text, so it FAILs official
                files merely for phrasing an error differently -> too noisy to be an oracle -> n/a
      v8        no native .wast runner here -> n/a
    """
    cwast = os.environ.get("CUSTOM_WAST_CMD")
    out = {}
    for en in engines:
        if en == "spec" and SPEC_WASM:
            out[en] = _native_wast([SPEC_WASM, src_path])
        elif en == "custom" and cwast:
            out[en] = _native_wast(shlex.split(cwast.format(wast=src_path)))
        else:
            out[en] = "n/a"
    return out


def detect_engines():
    eng = {}
    if NODE and os.path.exists(ORACLE):
        eng["v8"] = be_v8
    if shutil.which("wasmtime"):
        eng["wasmtime"] = be_wasmtime
    if os.path.exists(MC_RUNNER):                        # the wasmtime-crate embedder (exact bits, always gc-capable)
        eng["mcr"] = be_mc_runner
    if SPEC_WASM and os.path.exists(SPEC_WASM):          # the WebAssembly reference interpreter
        eng["spec"] = be_spec
    if os.environ.get("CUSTOM_CMD"):                     # any interpreter under test, no code change
        eng["custom"] = make_be_cmd(os.environ["CUSTOM_CMD"])
    return eng


ENGINES = detect_engines()
ENGINE_ORDER = ["v8", "wasmtime", "mcr", "spec", "custom"]
