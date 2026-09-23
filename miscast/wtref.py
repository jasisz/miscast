"""wasmtime as a reference oracle for tools/model_hunt.py: run a module through the local `wtdiff` build
(Cranelift O2) and return {export: signed i64 | "TRAP"}. Only meaningful for generators whose exports wtdiff
has already cross-checked across Cranelift O2 / O0, Winch and Pulley (exngen, memgen, intalg).
"""
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WTDIFF = os.environ.get("WTDIFF", os.path.join(ROOT, "work", "wtdiff", "target", "release", "wtdiff"))


def expected(wat, cfg="cl2"):
    with tempfile.NamedTemporaryFile("w", suffix=".wat", delete=False) as f:
        f.write(wat)
    try:
        out = subprocess.run([WTDIFF, f.name, "--cfgs", cfg], capture_output=True, text=True, check=True).stdout
    finally:
        os.remove(f.name)
    res = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] != cfg:
            continue
        _, export, val = parts
        if val.startswith("i64:"):
            res[export] = int(val[4:])
        elif val.startswith("TRAP"):
            res[export] = "TRAP"
        else:
            raise ValueError(f"wtref: unsupported result {val!r} for {export}")
    return res


def expected_isolated(wat, cfg="cl2"):
    """Like `expected`, but each export runs in a fresh instance (other exports un-exported), matching engines
    whose CLI instantiates the module once per invoked export: no memory.grow / global state carries over."""
    names = re.findall(r'\(export "([^"]+)"\)', wat)
    res = {}
    for name in names:
        solo = re.sub(r'\(export "([^"]+)"\)', lambda m: m.group(0) if m.group(1) == name else "", wat)
        res.update(expected(solo, cfg))
    return res
