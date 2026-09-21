"""Paths and engine-binary discovery.

Only the standard library is used here; the actual work is done by the external
CLI tools wasm-tools, node, wasmtime, wasmedge and iwasm, located at import time.
"""
import os
import shlex
import shutil
import subprocess

PKG = os.path.dirname(os.path.abspath(__file__))     # the miscast/ package dir
ROOT = os.path.dirname(PKG)                           # the repo root
WORK = os.path.join(ROOT, "work")
os.makedirs(WORK, exist_ok=True)

SEEDS_DEFAULT = os.path.join(ROOT, "seeds")
FEATURES = "gc,function-references,reference-types,tail-call,bulk-memory,multi-value,extended-const,exceptions,memory64,multi-memory,simd,threads,shared-everything-threads"


def find_node():
    """Newest WasmGC-capable node (>=22) on PATH or under ~/.nvm, or None."""
    cands = []
    p = subprocess.run(["bash", "-lc", "command -v node"], capture_output=True, text=True).stdout.strip()
    if p:
        cands.append(p)
    nvm = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(nvm):
        cands += [os.path.join(nvm, d, "bin", "node") for d in sorted(os.listdir(nvm), reverse=True)]
    for c in cands:
        try:
            if int(subprocess.run([c, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0]) >= 22:
                return c
        except Exception:
            pass
    return None


ORACLE = os.environ.get("V8_ORACLE") or os.path.join(PKG, "oracle", "v8.js")
NODE = os.environ.get("NODE") or find_node()
D8 = os.environ.get("D8")
D8_ORACLE = os.environ.get("D8_ORACLE") or os.path.join(PKG, "oracle", "d8.js")
D8_WORKER_ORACLE = os.environ.get("D8_WORKER_ORACLE") or os.path.join(PKG, "oracle", "d8_workers.js")
SPEC_WASM = os.environ.get("SPEC_WASM")          # path to the WebAssembly reference interpreter `wasm` binary
WASMEDGE = os.environ.get("WASMEDGE") or "wasmedge"
WASMEDGE_FLAGS = os.environ.get("WASMEDGE_FLAGS", "")
IWASM = os.environ.get("IWASM") or shutil.which("iwasm")
# iwasm otherwise appends an embedding-only application heap to modules in some
# build configurations, making bytes beyond the declared Wasm memory visible.
WAMR_FLAGS = shlex.split(os.environ.get("WAMR_FLAGS", "--heap-size=0 --interp"))


def tool_versions(engine_names):
    """One-line provenance — toolchain + engine versions actually used — so a report is reproducible."""
    def first_line(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0].strip()
        except Exception:
            return "?"
    out = ["wasm-tools=" + first_line(["wasm-tools", "--version"]).split()[-1]]
    if "v8" in engine_names and NODE:
        out.append("node=" + first_line([NODE, "--version"]))
    if "d8" in engine_names and D8:
        out.append("d8=" + first_line([D8, "--version"]).removeprefix("V8 version "))
    if "wasmtime" in engine_names:
        wt = first_line(["wasmtime", "--version"]).split()    # "wasmtime 43.0.0 (hash date)"
        out.append("wasmtime=" + (wt[1] if len(wt) > 1 else "?"))
    if "wasmedge" in engine_names:
        we = first_line([WASMEDGE, "--version"]).split()      # "wasmedge version 0.16.3"
        out.append("wasmedge=" + (we[2] if len(we) > 2 and we[1] == "version" else "?"))
    if "wamr" in engine_names and IWASM:
        wr = first_line([IWASM, "--version"]).split()         # "iwasm 2.4.5"
        out.append("wamr=" + (wr[1] if len(wr) > 1 else "?"))
    if "spec" in engine_names and SPEC_WASM:
        out.append("ref-interp")
    return "  ".join(out)
