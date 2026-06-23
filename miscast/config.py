"""Paths and engine-binary discovery.

Only the standard library is used here; the actual work is done by the external
CLI tools wasm-tools, node and wasmtime, located at import time.
"""
import os
import subprocess

PKG = os.path.dirname(os.path.abspath(__file__))     # the miscast/ package dir
ROOT = os.path.dirname(PKG)                           # the repo root
WORK = os.path.join(ROOT, "work")
os.makedirs(WORK, exist_ok=True)

SEEDS_DEFAULT = os.path.join(ROOT, "seeds")
FEATURES = "gc,function-references,reference-types,tail-call,bulk-memory,multi-value,extended-const"


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
SPEC_WASM = os.environ.get("SPEC_WASM")          # path to the WebAssembly reference interpreter `wasm` binary
