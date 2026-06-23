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

_NUM = re.compile(r"-?(?:\d+\.\d+|\d+|inf|nan)")   # an integer or float value at the start of output


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


def make_be_cmd(tmpl):
    """A backend for any interpreter, driven by a command template with {wat}/{wasm}/{export}."""
    def be(wat, wasm, export, args):
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


def detect_engines():
    eng = {}
    if NODE and os.path.exists(ORACLE):
        eng["v8"] = be_v8
    if shutil.which("wasmtime"):
        eng["wasmtime"] = be_wasmtime
    if SPEC_WASM and os.path.exists(SPEC_WASM):          # the WebAssembly reference interpreter
        eng["spec"] = be_spec
    if os.environ.get("CUSTOM_CMD"):                     # any interpreter under test, no code change
        eng["custom"] = make_be_cmd(os.environ["CUSTOM_CMD"])
    return eng


ENGINES = detect_engines()
ENGINE_ORDER = ["v8", "wasmtime", "spec", "custom"]
