#!/usr/bin/env python3
"""Run optstate probes through distinct V8 and Wasmtime compiler pipelines."""

import argparse
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miscast.optstate import optstate_gen  # noqa: E402
from miscast.toolchain import prepare  # noqa: E402


D8_CONFIGS = {
    "liftoff": ("--liftoff-only",),
    "top-tier": ("--no-liftoff",),
    "tiered-load-elim": ("--wasm-sync-tier-up", "--wasm-tiering-budget=100"),
    "tiered-no-load-elim": (
        "--wasm-sync-tier-up", "--wasm-tiering-budget=100", "--no-turboshaft-wasm-load-elimination",
    ),
    "future-ts": (
        "--no-liftoff", "--turboshaft-loop-optimization", "--turboshaft-typed-optimizations",
    ),
}
WASMTIME_CONFIGS = {
    "cranelift-0-copying": ("-C", "compiler=cranelift,collector=copying", "-O", "gc-zeal-alloc-counter=1,opt-level=0"),
    "cranelift-2-copying": ("-C", "compiler=cranelift,collector=copying", "-O", "gc-zeal-alloc-counter=1,opt-level=2"),
    "cranelift-s-copying": ("-C", "compiler=cranelift,collector=copying", "-O", "gc-zeal-alloc-counter=1,opt-level=s"),
    "cranelift-2-drc": ("-C", "compiler=cranelift,collector=drc", "-O", "gc-zeal-alloc-counter=1,opt-level=2"),
}


def run_d8(case, config, d8, oracle):
    seed, label, expected, wasm = case
    proc = subprocess.run([d8, *D8_CONFIGS[config], str(oracle), "--", wasm, "f"],
                          capture_output=True, text=True, timeout=30)
    match = re.search(r"=> OK (-?\d+)", proc.stdout)
    actual = f"OK {match.group(1)}" if match else (proc.stdout + proc.stderr).strip()[-800:]
    failure = None if proc.returncode == 0 and actual == expected else (
        f"seed={seed} {label}: expected {expected}, got {actual or f'exit {proc.returncode}'}"
    )
    return ("d8", config), failure


def run_wasmtime(case, config, wasmtime):
    seed, label, expected, wasm = case
    proc = subprocess.run([
        wasmtime, "run", "--invoke", "f", "-W", "function-references=y,gc=y",
        *WASMTIME_CONFIGS[config], wasm,
    ], capture_output=True, text=True, timeout=30)
    numbers = re.findall(r"^-?\d+$", proc.stdout, re.MULTILINE)
    actual = f"OK {numbers[-1]}" if numbers else (proc.stdout + proc.stderr).strip()[-800:]
    failure = None if proc.returncode == 0 and actual == expected else (
        f"seed={seed} {label}: expected {expected}, got {actual or f'exit {proc.returncode}'}"
    )
    return ("wasmtime", config), failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--cases", type=int, default=60)
    parser.add_argument("-j", "--jobs", type=int, default=10)
    parser.add_argument("--d8", default=os.environ.get("D8", str(Path.home() / ".jsvu/bin/v8")))
    parser.add_argument("--wasmtime", default=os.environ.get("WASMTIME", "wasmtime"))
    args = parser.parse_args()
    oracle = ROOT / "miscast" / "oracle" / "d8.js"

    cases = []
    for seed in range(args.cases):
        label, _export, expected, wat = optstate_gen(seed)
        _wat, wasm, valid = prepare(wat)
        if not valid:
            parser.error(f"invalid generated module: seed={seed} {label}")
        cases.append((seed, label, expected, wasm))

    keys = [("d8", config) for config in D8_CONFIGS] + [("wasmtime", config) for config in WASMTIME_CONFIGS]
    failures = {key: [] for key in keys}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_d8, case, config, args.d8, oracle)
                   for case in cases for config in D8_CONFIGS]
        futures += [pool.submit(run_wasmtime, case, config, args.wasmtime)
                    for case in cases for config in WASMTIME_CONFIGS]
        for future in concurrent.futures.as_completed(futures):
            key, failure = future.result()
            if failure:
                failures[key].append(failure)

    failed = 0
    for key in keys:
        bad = failures[key]
        failed += len(bad)
        print(f"{key[0]:8} {key[1]:20} {len(cases) - len(bad)}/{len(cases)} passed")
        for detail in bad:
            print(f"  {detail}")
    total = len(cases) * len(keys)
    print(f"total: {total - failed}/{total} passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
