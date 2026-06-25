"""Dedupe and minimize findings so a hunt produces reportable bugs, not a wall of duplicates.

A mutate run emits one finding per VARIANT, but dozens of mutation labels over the same seed are the same
underlying bug — a 937-row Talos run was really a handful of distinct defects. `dedupe` collapses findings
by base case + divergence shape so the count means something. `minimize` shrinks a single failing module
to a minimal reproducer via `wasm-tools shrink`, and `repro_metadata` records the environment a maintainer
needs (engine versions, arch) so the finding can actually be filed.
"""
import os
import platform
import subprocess
from collections import OrderedDict


def finding_key(name, verdicts, verdict):
    """Dedup key: the base case (before the mutation label) plus the exact divergence shape (the verdict
    and every engine's result). Different mutation labels with the same shape are one bug; a different
    value or a different engine split is a distinct finding."""
    base = name.split("|", 1)[0]
    shape = (verdict, tuple(sorted(verdicts.items())))
    return (base, shape)


def dedupe(records):
    """records: iterable of (name, verdicts_dict, verdict). Returns a list of
    {key, representative, count, verdict, base} ordered by first appearance — one entry per distinct bug."""
    groups = OrderedDict()
    for name, verdicts, verdict in records:
        k = finding_key(name, verdicts, verdict)
        g = groups.get(k)
        if g is None:
            groups[k] = {"key": k, "representative": name, "count": 1,
                         "verdict": verdict, "base": k[0]}
        else:
            g["count"] += 1
    return list(groups.values())


def dedupe_summary(records):
    """A one-line-per-distinct-bug text block plus the (total, distinct) counts."""
    groups = dedupe(records)
    lines = [f"  {g['verdict']:11} {g['base']:44} x{g['count']:<4} (e.g. {g['representative']})"
             for g in groups]
    total = sum(g["count"] for g in groups)
    return "\n".join(lines), total, len(groups)


def minimize(wasm_path, predicate_path, out_path):
    """Shrink `wasm_path` with `wasm-tools shrink` while `predicate_path` (an executable that exits 0 when
    the input is still interesting, e.g. still diverges) holds. Returns out_path on success, else None."""
    if not os.path.exists(predicate_path):
        return None
    r = subprocess.run(["wasm-tools", "shrink", predicate_path, wasm_path, "-o", out_path],
                       capture_output=True, text=True)
    return out_path if r.returncode == 0 and os.path.exists(out_path) else None


def _tool_version(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    except Exception:
        return "unknown"


def repro_metadata():
    """Environment a maintainer needs to act on a finding: arch + the tool versions in play."""
    return {
        "arch": f"{platform.system()}/{platform.machine()}",
        "wasm-tools": _tool_version(["wasm-tools", "--version"]),
        "wasmtime": _tool_version(["wasmtime", "--version"]),
    }
