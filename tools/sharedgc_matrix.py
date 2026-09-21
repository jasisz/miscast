#!/usr/bin/env python3
"""Run Shared-Everything GC probes through distinct V8 compiler pipelines."""

import argparse
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miscast.sharedgc import sharedgc_gen  # noqa: E402
from miscast.toolchain import prepare  # noqa: E402


D8_CONFIGS = {
    "baseline": ("--wasm-shared",),
    "liftoff": ("--wasm-shared", "--liftoff-only"),
    "top-tier": ("--wasm-shared", "--no-liftoff"),
    "tiered": ("--wasm-shared", "--wasm-sync-tier-up", "--wasm-tiering-budget=100"),
    "tiered-no-load-elim": (
        "--wasm-shared", "--wasm-sync-tier-up", "--wasm-tiering-budget=100",
        "--no-turboshaft-wasm-load-elimination",
    ),
    "top-tier-gc-stress": (
        "--wasm-shared", "--no-liftoff", "--gc-interval=16", "--stress-scavenge=50",
        "--stress-compaction",
    ),
}


def run_case(case, config, d8, oracle):
    seed, label, expected, wasm = case
    proc = subprocess.run(
        [d8, *D8_CONFIGS[config], str(oracle), "--", wasm, "f"],
        capture_output=True, text=True, timeout=30,
    )
    match = re.search(r"=> OK (-?\d+)", proc.stdout)
    if match:
        actual = f"OK {match.group(1)}"
    elif "=> TRAP" in proc.stdout:
        actual = "TRAP"
    else:
        actual = (proc.stdout + proc.stderr).strip()[-800:]
    failure = None if proc.returncode == 0 and actual == expected else (
        f"seed={seed} {label}: expected {expected}, got {actual or f'exit {proc.returncode}'}"
    )
    return config, failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--cases", type=int, default=56)
    parser.add_argument("-j", "--jobs", type=int, default=8)
    parser.add_argument("--d8", default=os.environ.get("D8", str(Path.home() / ".jsvu/bin/v8")))
    args = parser.parse_args()
    oracle = ROOT / "miscast" / "oracle" / "d8.js"

    cases = []
    for seed in range(args.cases):
        label, _export, expected, wat = sharedgc_gen(seed)
        _wat, wasm, valid = prepare(wat)
        if not valid:
            parser.error(f"invalid generated module: seed={seed} {label}")
        cases.append((seed, label, expected, wasm))

    failures = {config: [] for config in D8_CONFIGS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_case, case, config, args.d8, oracle)
                   for case in cases for config in D8_CONFIGS]
        for future in concurrent.futures.as_completed(futures):
            config, failure = future.result()
            if failure:
                failures[config].append(failure)

    failed = 0
    for config, bad in failures.items():
        failed += len(bad)
        print(f"d8 {config:21} {len(cases) - len(bad)}/{len(cases)} passed")
        for detail in bad:
            print(f"  {detail}")
    total = len(cases) * len(D8_CONFIGS)
    print(f"total: {total - failed}/{total} passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
