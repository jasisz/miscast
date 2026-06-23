"""wasm-tools wrappers: a cached prepare() (assemble + validate) and a u32 helper.

prepare() is memoized by module text — a .wast module is shared across all of its
invoke-cases, so each distinct module is assembled and validated only once even
though many cases reference it. Thread-safe (the run loop is a thread pool): a
per-key lock lets distinct modules prepare concurrently while a shared one runs
the toolchain exactly once.
"""
import hashlib
import subprocess
import threading

from .config import WORK, FEATURES


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


_prepared = {}
_locks = {}
_guard = threading.Lock()


def _lock_for(key):
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def prepare(wat):
    """Assemble + validate `wat`, memoized by text. Returns (wat_path, wasm_path|None, valid)."""
    key = hashlib.sha1(wat.encode()).hexdigest()[:16]
    hit = _prepared.get(key)
    if hit is not None:
        return hit
    with _lock_for(key):
        hit = _prepared.get(key)
        if hit is not None:
            return hit
        wp, wsm = f"{WORK}/m_{key}.wat", f"{WORK}/m_{key}.wasm"
        open(wp, "w").write(wat)
        ok = _run(["wasm-tools", "parse", wp, "-o", wsm]).returncode == 0
        wsm = wsm if ok else None
        valid = bool(wsm) and _run(["wasm-tools", "validate", wsm, f"--features={FEATURES}"]).returncode == 0
        result = (wp, wsm, valid)
        _prepared[key] = result
        return result


def u32(s):
    try:
        return str(int(s, 0) & 0xffffffff)
    except (ValueError, TypeError):
        return s
