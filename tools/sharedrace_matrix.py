#!/usr/bin/env python3
"""Repeat multi-worker Shared-Everything races across V8 compiler pipelines."""

import argparse
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miscast.sharedrace import sharedrace_gen  # noqa: E402
from miscast.toolchain import prepare  # noqa: E402


D8_CONFIGS = {
    "baseline": ("--wasm-shared",),
    "liftoff": ("--wasm-shared", "--liftoff-only"),
    "top-tier": ("--wasm-shared", "--no-liftoff"),
    "tiered": ("--wasm-shared", "--wasm-sync-tier-up", "--wasm-tiering-budget=100"),
    "top-tier-gc-stress": (
        "--wasm-shared", "--no-liftoff", "--gc-interval=16",
        "--stress-scavenge=50", "--stress-compaction",
    ),
}


def run_case(case, config, repetition, d8, oracle):
    seed, label, expected, wasm = case
    proc = subprocess.run(
        [d8, *D8_CONFIGS[config], str(oracle), "--", wasm, "__shared_workers__"],
        capture_output=True, text=True, timeout=120,
    )
    match = re.search(r"=> OK (-?\d+)", proc.stdout)
    actual = f"OK {match.group(1)}" if match else (proc.stdout + proc.stderr).strip()[-800:]
    failure = None if proc.returncode == 0 and actual == expected else (
        f"repeat={repetition} seed={seed} {label}: expected {expected}, got {actual or f'exit {proc.returncode}'}"
    )
    return config, failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--cases", type=int, default=80)
    parser.add_argument("-r", "--repetitions", type=int, default=3)
    parser.add_argument("-j", "--jobs", type=int, default=8)
    parser.add_argument("--d8", default=os.environ.get("D8", str(Path.home() / ".jsvu/bin/v8")))
    args = parser.parse_args()
    oracle = ROOT / "miscast" / "oracle" / "d8_workers.js"

    cases = []
    for seed in range(args.cases):
        label, _export, expected, wat = sharedrace_gen(seed)
        _wat, wasm, valid = prepare(wat)
        if not valid:
            parser.error(f"invalid generated module: seed={seed} {label}")
        cases.append((seed, label, expected, wasm))

    failures = {config: [] for config in D8_CONFIGS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_case, case, config, repetition, args.d8, oracle)
                   for repetition in range(args.repetitions)
                   for case in cases for config in D8_CONFIGS]
        for future in concurrent.futures.as_completed(futures):
            config, failure = future.result()
            if failure:
                failures[config].append(failure)

    per_config = len(cases) * args.repetitions
    failed = 0
    for config, bad in failures.items():
        failed += len(bad)
        print(f"d8 {config:21} {per_config - len(bad)}/{per_config} passed")
        for detail in bad:
            print(f"  {detail}")
    total = per_config * len(D8_CONFIGS)
    print(f"total: {total - failed}/{total} passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
