#!/usr/bin/env python3
"""Run deterministic linear-memory atomics through V8 and Wasmtime pipelines."""

import argparse
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miscast.atomicedge import atomicedge_gen  # noqa: E402
from miscast.toolchain import prepare  # noqa: E402


D8_CONFIGS = {
    "baseline": (),
    "liftoff": ("--liftoff-only",),
    "top-tier": ("--no-liftoff",),
    "tiered": ("--wasm-sync-tier-up", "--wasm-tiering-budget=100"),
    "tiered-no-load-elim": (
        "--wasm-sync-tier-up", "--wasm-tiering-budget=100", "--no-turboshaft-wasm-load-elimination",
    ),
}
WASMTIME_CONFIGS = {
    "cranelift-0": ("-C", "compiler=cranelift", "-O", "opt-level=0"),
    "cranelift-1": ("-C", "compiler=cranelift", "-O", "opt-level=1"),
    "cranelift-2": ("-C", "compiler=cranelift", "-O", "opt-level=2"),
    "cranelift-s": ("-C", "compiler=cranelift", "-O", "opt-level=s"),
    "explicit-bounds": (
        "-C", "compiler=cranelift", "-O",
        "opt-level=2,memory-reservation=131072,memory-reservation-for-growth=0,memory-guard-size=0,memory-may-move=y",
    ),
    "signal-guards": (
        "-C", "compiler=cranelift", "-O",
        "opt-level=2,memory-reservation=4294967296,memory-guard-size=65536,signals-based-traps=y",
    ),
}


def actual_from(proc):
    numbers = re.findall(r"^-?\d+$", proc.stdout, re.MULTILINE)
    if "=> OK " in proc.stdout:
        match = re.search(r"=> OK (-?\d+)", proc.stdout)
        return f"OK {match.group(1)}" if match else proc.stdout.strip()[-800:]
    if numbers:
        return f"OK {numbers[-1]}"
    both = proc.stdout + proc.stderr
    if "=> TRAP" in proc.stdout or "wasm trap" in both.lower():
        return "TRAP"
    return both.strip()[-800:]


def run_d8(case, config, d8, oracle):
    seed, label, expected, wasm = case
    proc = subprocess.run([d8, *D8_CONFIGS[config], str(oracle), "--", wasm, "f"],
                          capture_output=True, text=True, timeout=30)
    actual = actual_from(proc)
    failure = None if actual == expected else f"seed={seed} {label}: expected {expected}, got {actual}"
    return ("d8", config), failure


def run_wasmtime(case, config, wasmtime):
    seed, label, expected, wasm = case
    proc = subprocess.run([
        wasmtime, "run", "--invoke", "f", "-W", "threads=y,shared-memory=y",
        *WASMTIME_CONFIGS[config], wasm,
    ], capture_output=True, text=True, timeout=30)
    actual = actual_from(proc)
    failure = None if actual == expected else f"seed={seed} {label}: expected {expected}, got {actual}"
    return ("wasmtime", config), failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--cases", type=int, default=56)
    parser.add_argument("-j", "--jobs", type=int, default=10)
    parser.add_argument("--d8", default=os.environ.get("D8", str(Path.home() / ".jsvu/bin/v8")))
    parser.add_argument("--wasmtime", default=os.environ.get("WASMTIME", "wasmtime"))
    args = parser.parse_args()
    oracle = ROOT / "miscast" / "oracle" / "d8.js"

    cases = []
    for seed in range(args.cases):
        label, _export, expected, wat = atomicedge_gen(seed)
        _wat, wasm, valid = prepare(wat)
        if not valid:
            parser.error(f"invalid generated module: seed={seed} {label}")
        cases.append((seed, label, expected, wasm))

    keys = [("d8", c) for c in D8_CONFIGS] + [("wasmtime", c) for c in WASMTIME_CONFIGS]
    failures = {key: [] for key in keys}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_d8, case, c, args.d8, oracle) for case in cases for c in D8_CONFIGS]
        futures += [pool.submit(run_wasmtime, case, c, args.wasmtime) for case in cases for c in WASMTIME_CONFIGS]
        for future in concurrent.futures.as_completed(futures):
            key, failure = future.result()
            if failure:
                failures[key].append(failure)

    failed = 0
    for key in keys:
        bad = failures[key]
        failed += len(bad)
        print(f"{key[0]:8} {key[1]:21} {len(cases) - len(bad)}/{len(cases)} passed")
        for detail in bad:
            print(f"  {detail}")
    total = len(cases) * len(keys)
    print(f"total: {total - failed}/{total} passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
