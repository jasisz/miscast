"""Command-line entry point: parse args, run the corpus, print the table + summary."""
import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .config import SEEDS_DEFAULT, WORK, tool_versions
from .engines import ENGINES, ENGINE_ORDER
from .modes import MODES
from .wast import load_corpus
from .runner import differential
from .repro import write_repro

# Verdict -> severity class, in report order. The class names ARE the headline taxonomy.
SEVERITY = {
    "SOUNDNESS":    "SOUNDNESS",      # SUT runs what every oracle traps/rejects — the dangerous class
    "VALUE":        "VALUE",          # SUT returns a different value than the oracles agree on
    "completeness": "OVER_TRAP",      # SUT traps where the oracles run (its own limitation)
    "sut-reject":   "OVER_REJECT",    # SUT errors/refuses where the oracles run (over-rejection)
    "oracle-split": "ORACLE_SPLIT",   # the oracles disagree — a confounder, never a finding
    "oracle-unsup": "ORACLE_UNSUP",   # no oracle could run the case
    "assemble-fail": "HARNESS",       # wasm-tools could not assemble the module
    "invalid":      "HARNESS",        # module is invalid (validation-differential territory)
    "agree":        "AGREE",
}


def main():
    ap = argparse.ArgumentParser(prog="miscast",
                                 description="a .wast-native differential tester for WebAssembly GC subtype soundness")
    ap.add_argument("--mode", choices=MODES, default="replay")
    ap.add_argument("--seeds", default=SEEDS_DEFAULT, help="dir of .wast / .wat corpus")
    ap.add_argument("--sut", required=True, help="engine under test (e.g. wasmtime, custom); the rest are oracles")
    ap.add_argument("--oracles", help="oracle engines to use, comma-separated (the SUT is always "
                                      "run alongside; default: all other detected engines)")
    ap.add_argument("-n", type=int, default=300, help="smith module count")
    ap.add_argument("--overtrap", action="store_true",
                    help="also flag completeness divergences — the SUT traps, or errors/refuses, where "
                         "the oracles run; off by default (the SUT's own limitation, not unsoundness)")
    ap.add_argument("-j", "--jobs", type=int, default=min(32, (os.cpu_count() or 4) * 4),
                    help="parallel workers (cases are independent subprocess calls)")
    args = ap.parse_args()

    if not ENGINES:
        sys.exit("no engines detected (need node+oracle, wasmtime, or SPEC_WASM/CUSTOM_CMD)")
    if args.sut not in ENGINES:
        sys.exit(f"--sut {args.sut} not available; detected: {', '.join(ENGINES) or '(none)'}")
    engines = dict(ENGINES)
    if args.oracles:                               # --oracles names the oracle set; the SUT is always run
        want = [e.strip() for e in args.oracles.split(",") if e.strip()]
        missing = [e for e in want if e not in ENGINES]
        if missing:
            sys.exit(f"engines not available: {', '.join(missing)}; detected: {', '.join(ENGINES)}")
        engines = {e: ENGINES[e] for e in want}
        engines[args.sut] = ENGINES[args.sut]
    cols = [e for e in ENGINE_ORDER if e in engines and e != args.sut] + [args.sut]

    cases, stats = load_corpus(args.seeds)
    work, untested = MODES[args.mode](cases, args.n)
    print(f"# mode={args.mode}  files={stats['files']}  modules={stats['modules']}  "
          f"cases={len(work)}  (validation-only not run={stats['invalid']}, unrunnable={stats['skip']})")
    print(f"# engines={'+'.join(cols)}  sut={args.sut}  jobs={args.jobs}")
    print(f"# tools={tool_versions(engines)}")
    print(f"\n{'case':44} " + " ".join(f"{c:11}" for c in cols) + f" {'assert':8} verdict")
    print("-" * (46 + 12 * len(cols) + 20))
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(lambda c: differential(c, args.sut, engines), work))

    classes = Counter()
    log, repro_dirs = [], []
    for nm, verdicts, expected, verdict, isdiv, repro in results:
        cls = SEVERITY.get(verdict, verdict)
        classes[cls] += 1
        if verdict in ("assemble-fail", "invalid"):
            continue
        counted = isdiv and (args.overtrap or verdict not in ("completeness", "sut-reject"))
        row = " ".join(f"{verdicts.get(c, '-'):11}" for c in cols)
        print(f"{nm:44} {row} {(expected or '-'):8} {verdict}{'   <<<' if counted else ''}")
        if counted:
            log.append(f"{nm}: {verdicts} assert={expected} [{verdict}]")
            repro_dirs.append(write_repro(nm, repro, verdicts, expected, cols))

    print("-" * (46 + 12 * len(cols) + 20))
    findings = classes["SOUNDNESS"] + classes["VALUE"]
    print(f"FINDINGS={findings}  SOUNDNESS={classes['SOUNDNESS']}  VALUE={classes['VALUE']}")
    print("  by class: " + "  ".join(f"{k}={classes[k]}" for k in
          ("SOUNDNESS", "VALUE", "OVER_TRAP", "OVER_REJECT", "ORACLE_SPLIT", "ORACLE_UNSUP", "HARNESS", "AGREE")
          if classes[k]))
    if untested:
        print(f"!! mutate: {len(untested)} module(s) had no sweepable op")
    if log:
        with open(os.path.join(WORK, "divergences.txt"), "w") as f:
            f.write("\n".join(log) + "\n")
        print(f"# {len(log)} finding(s) -> {os.path.join(WORK, 'divergences.txt')}")
        print(f"# reproducers   -> {os.path.join(WORK, 'repro')}/  ({len(repro_dirs)} dir(s))")
