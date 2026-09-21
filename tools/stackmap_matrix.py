#!/usr/bin/env python3
"""Run stackmap cases across Wasmtime collector and optimizer configurations."""

import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miscast.stackmap import stackmap_gen  # noqa: E402
from miscast.toolchain import prepare  # noqa: E402


RUNNER = ROOT / "runner" / "target" / "release" / "mc-runner"
CONFIGS = tuple(
    (collector, opt, collector != "null")
    for collector in ("drc", "copying", "null")
    for opt in ("0", "2", "s")
)


def run_one(case, config):
    seed, label, export, expected, wasm = case
    collector, opt, stress = config
    cmd = [
        str(RUNNER),
        wasm,
        "--invoke",
        export,
        "--collector",
        collector,
        "--opt",
        opt,
    ]
    if stress:
        cmd.append("--gc-stress")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(proc.stdout)
        actual = data.get("results", [None])[0]
        wanted = f"i32:{expected.removeprefix('OK ')}"
        if proc.returncode == 0 and data.get("ok") and actual == wanted:
            return config, None
        detail = data.get("error") or actual or proc.stderr.strip() or f"exit {proc.returncode}"
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        detail = str(exc)
    return config, f"seed={seed} {label}: expected {expected}, got {detail}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--cases", type=int, default=30)
    parser.add_argument("-j", "--jobs", type=int, default=6)
    args = parser.parse_args()

    if not RUNNER.is_file():
        parser.error(f"missing {RUNNER}; build it with cargo build --release --manifest-path runner/Cargo.toml")

    cases = []
    for seed in range(args.cases):
        label, export, expected, wat = stackmap_gen(seed)
        _wat_path, wasm, valid = prepare(wat)
        if not valid:
            parser.error(f"generated invalid module for seed {seed}")
        cases.append((seed, label, export, expected, wasm))

    failures = {config: [] for config in CONFIGS}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, case, config) for case in cases for config in CONFIGS]
        for future in concurrent.futures.as_completed(futures):
            config, failure = future.result()
            if failure:
                failures[config].append(failure)

    total = len(cases) * len(CONFIGS)
    failed = sum(map(len, failures.values()))
    for collector, opt, stress in CONFIGS:
        bad = failures[(collector, opt, stress)]
        suffix = "+gc-stress" if stress else ""
        print(f"{collector:7} opt={opt} {suffix:10} {len(cases) - len(bad)}/{len(cases)} passed")
        for detail in bad:
            print(f"  {detail}")
    print(f"total: {total - failed}/{total} passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
