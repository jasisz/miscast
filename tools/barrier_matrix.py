#!/usr/bin/env python3
"""Run write-barrier probes across Wasmtime collectors/optimizers and a current d8."""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miscast.barrier import barrier_gen  # noqa: E402
from miscast.toolchain import prepare  # noqa: E402


RUNNER = ROOT / "runner" / "target" / "release" / "mc-runner"
CONFIGS = tuple(
    (collector, opt, collector != "null")
    for collector in ("drc", "copying", "null")
    for opt in ("0", "2", "s")
)
D8_FLAGS = (
    "--wasm-sync-tier-up", "--wasm-tiering-budget=100", "--gc-interval=16",
    "--stress-scavenge=50", "--stress-compaction",
)


def run_wasmtime(case, config):
    seed, label, export, expected, wasm = case
    collector, opt, stress = config
    cmd = [str(RUNNER), wasm, "--invoke", export, "--collector", collector, "--opt", opt]
    if stress:
        cmd.append("--gc-stress")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(proc.stdout)
        actual = data.get("results", [None])[0]
        wanted = f"i32:{expected.removeprefix('OK ')}"
        if proc.returncode == 0 and data.get("ok") and actual == wanted:
            return ("wasmtime", config), None
        detail = data.get("error") or actual or proc.stderr.strip() or f"exit {proc.returncode}"
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        detail = str(exc)
    return ("wasmtime", config), f"seed={seed} {label}: expected {expected}, got {detail}"


def run_d8(case, d8, harness):
    seed, label, _export, expected, wasm = case
    try:
        proc = subprocess.run([d8, *D8_FLAGS, str(harness), "--", wasm],
                              capture_output=True, text=True, timeout=30)
        numbers = re.findall(r"^-?\d+$", proc.stdout, re.MULTILINE)
        actual = f"OK {numbers[-1]}" if numbers else None
        if proc.returncode == 0 and actual == expected:
            return ("d8", None), None
        detail = (proc.stdout + proc.stderr).strip()[-800:] or f"exit {proc.returncode}"
    except subprocess.TimeoutExpired as exc:
        detail = str(exc)
    return ("d8", None), f"seed={seed} {label}: expected {expected}, got {detail}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--cases", type=int, default=30)
    parser.add_argument("-j", "--jobs", type=int, default=9)
    parser.add_argument("--d8", default=os.environ.get("D8", str(Path.home() / ".jsvu/bin/v8")))
    args = parser.parse_args()

    if not RUNNER.is_file():
        parser.error(f"missing {RUNNER}; build it with cargo build --release --manifest-path runner/Cargo.toml")
    if not Path(args.d8).is_file():
        parser.error(f"missing d8: {args.d8}")

    harness = ROOT / "work" / "_barrier_d8.js"
    harness.write_text(
        "const b=readbuffer(arguments[0]);"
        "const m=new WebAssembly.Module(b);"
        "const i=new WebAssembly.Instance(m,{});"
        "print(i.exports.f());\n"
    )
    cases = []
    for seed in range(args.cases):
        label, export, expected, wat = barrier_gen(seed)
        _wat_path, wasm, valid = prepare(wat)
        if not valid:
            parser.error(f"generated invalid module for seed {seed}")
        cases.append((seed, label, export, expected, wasm))

    keys = [("wasmtime", config) for config in CONFIGS] + [("d8", None)]
    failures = {key: [] for key in keys}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_wasmtime, case, config) for case in cases for config in CONFIGS]
        futures += [pool.submit(run_d8, case, args.d8, harness) for case in cases]
        for future in concurrent.futures.as_completed(futures):
            key, failure = future.result()
            if failure:
                failures[key].append(failure)

    total, failed = len(cases) * len(keys), sum(map(len, failures.values()))
    for collector, opt, stress in CONFIGS:
        bad = failures[("wasmtime", (collector, opt, stress))]
        suffix = "+gc-stress" if stress else ""
        print(f"wasmtime {collector:7} opt={opt} {suffix:10} {len(cases) - len(bad)}/{len(cases)} passed")
        for detail in bad:
            print(f"  {detail}")
    d8_bad = failures[("d8", None)]
    version = subprocess.run([args.d8, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"d8 {version:18} {'':21} {len(cases) - len(d8_bad)}/{len(cases)} passed")
    for detail in d8_bad:
        print(f"  {detail}")
    print(f"total: {total - failed}/{total} passed")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
