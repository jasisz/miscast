"""Command-line entry point: parse args, run the corpus, print the tables + summary.

replay runs three differential sections over the faithful .wast command stream:
  execution    per-action value/trap differential (non-stateful run-segment actions)
  validation   assert_invalid — does the SUT REJECT a module the spec marks invalid?
  conformance  stateful .wast run natively (spec + wasmtime), state preserved
mutate breaks a type relationship in each seed, then routes by validity: valid variants to the
  execution differential, ill-typed variants to the validation differential (does the SUT reject?).
smith generates random valid modules and runs the execution section.
"""
import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .config import SEEDS_DEFAULT, WORK, tool_versions
from .engines import ENGINES, ENGINE_ORDER, wasmtime_has_gc
from .modes import MODES
from .invalid import segs as invalid_battery
from .mutate import mutate_module
from .reduce import dedupe_summary
from .toolchain import prepare
from .wast import load_corpus, load_script_corpus
from .runner import differential, validation_differential, conformance_differential
from .repro import write_repro

# Verdict -> severity class, in report order. The class names ARE the headline taxonomy.
SEVERITY = {
    "SOUNDNESS":     "SOUNDNESS",     # SUT runs/accepts what every oracle traps/rejects — the dangerous class
    "VALUE":         "VALUE",         # SUT returns a different value than the oracles agree on
    "completeness":  "OVER_TRAP",     # SUT traps where the oracles run (its own limitation)
    "sut-reject":    "OVER_REJECT",   # SUT errors/refuses where the oracles run (over-rejection)
    "oracle-split":  "ORACLE_SPLIT",  # the oracles disagree — a confounder, never a finding
    "oracle-unsup":  "ORACLE_UNSUP",  # no oracle could run the case
    "sut-unsup":     "SUT_UNSUP",     # SUT couldn't be probed (e.g. invalid module with no export)
    "sut-stateful-na": "SUT_NA",      # one-shot SUT can't execute a stateful script
    "assemble-fail": "HARNESS",       # wasm-tools could not assemble the module
    "invalid":       "HARNESS",       # module is invalid in the execution section (handled by validation)
    "agree":         "AGREE",
}
CLASS_ORDER = ("SOUNDNESS", "VALUE", "OVER_TRAP", "OVER_REJECT", "ORACLE_SPLIT",
               "ORACLE_UNSUP", "SUT_UNSUP", "SUT_NA", "HARNESS", "AGREE")


def _cell(v, w=11):
    """Keep the table aligned: a float printed in full (an e+300 literal expands to 300+ digits)
    would blow a column apart, so truncate the DISPLAY — the full value stays in the reproducer."""
    return v if len(v) <= w else v[:w - 1] + "…"


def main():
    ap = argparse.ArgumentParser(prog="miscast",
                                 description="a .wast-native differential tester for WebAssembly GC subtype soundness")
    ap.add_argument("--mode", choices=list(MODES) + ["invalid"], default="replay",
                    help="replay/mutate (corpus), smith (random), the self-checking GC oracles "
                         "morphism/recgroup/externconvert/castbr, invalid (spec-invalid validation battery), "
                         "or 'all' to run the whole oracle suite + the validation battery at once")
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
    sut = args.sut
    base_cols = [e for e in ENGINE_ORDER if e in engines and e != sut] + [sut]

    if "wasmtime" in engines and not wasmtime_has_gc():
        sub = ("the gc-capable `mcr` engine is available as a substitute" if "mcr" in engines
               else "use a gc-capable wasmtime (the official release binaries) or build runner/ (mcr)")
        print(f"# WARNING: this `wasmtime` was built without the gc feature — it rejects every GC module, "
              f"so it is not a GC oracle here; {sub}.", file=sys.stderr)

    classes = Counter()
    log, repro_dirs, records = [], [], []

    def section(title, results, cols):
        """Print one differential section; tally severity; collect findings + reproducers."""
        print(f"\n## {title}")
        print(f"{'case':44} " + " ".join(f"{c:11}" for c in cols) + f" {'assert':8} verdict")
        print("-" * (46 + 12 * len(cols) + 20))
        for nm, verdicts, expected, verdict, isfind, repro in results:
            classes[SEVERITY.get(verdict, verdict)] += 1
            if verdict in ("assemble-fail",):
                continue
            counted = isfind and (args.overtrap or verdict not in ("completeness", "sut-reject"))
            row = " ".join(f"{_cell(verdicts.get(c, '-')):11}" for c in cols)
            print(f"{nm:44} {row} {_cell(expected or '-', 8):8} {verdict}{'   <<<' if counted else ''}")
            if counted:
                log.append(f"[{title}] {nm}: {verdicts} -> {verdict}")
                records.append((nm, dict(verdicts), verdict))
                repro_dirs.append(write_repro(nm, repro, verdicts, expected, cols))

    def pool(label, fn, items):
        # a full-corpus run is minutes long; announce each section up front (flushed) so the
        # terminal isn't blank — the section's rows print when the pool drains.
        print(f"# running {label} ({len(items)} cases)…", flush=True)
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            return list(ex.map(fn, items))

    if args.mode == "replay":
        segs, stats = load_script_corpus(args.seeds)
        action_cases, invalid_segs, stateful_files = [], [], {}
        nstateful = 0
        for s in segs:
            if s["kind"] == "invalid":
                invalid_segs.append(s)
            elif s["stateful"]:
                nstateful += 1
                stateful_files[s["src"]] = stateful_files.get(s["src"], 0) + 1
            else:
                for a in s["actions"]:
                    action_cases.append((f"{s['name']}:{a['export']}", s["module"],
                                         a["export"], a["args"], a["expected"], a["rtype"]))
        stateful_groups = [{"name": os.path.basename(src), "src": src, "nstateful": n}
                           for src, n in sorted(stateful_files.items())]
        print(f"# mode=replay  files={stats['files']}  modules={stats['modules']}  "
              f"actions={len(action_cases)}  invalid={len(invalid_segs)}  "
              f"stateful={nstateful} in {len(stateful_groups)} file(s)  "
              f"(malformed not run={stats['malformed']}, unrunnable={stats['skip']})")
        print(f"# engines={'+'.join(base_cols)}  sut={sut}  jobs={args.jobs}")
        print(f"# tools={tool_versions(engines)}", flush=True)
        section("execution — per-action value / trap differential",
                pool("execution", lambda c: differential(c, sut, engines), action_cases), base_cols)
        if invalid_segs:
            section("validation — assert_invalid (does the SUT reject an ill-typed module?)",
                    pool("validation", lambda s: validation_differential(s, sut, engines), invalid_segs),
                    ["wtools"] + base_cols)
        if stateful_groups:
            section("conformance — whole stateful .wast files run natively on the reference interpreter",
                    pool("conformance", lambda g: conformance_differential(g, sut, engines), stateful_groups),
                    base_cols)
    elif args.mode == "mutate":
        cases, stats = load_corpus(args.seeds)
        variants, untested = [], 0
        for name, mod, export, eargs, _exp, rtype in cases:
            vs = mutate_module(mod)
            if not vs:
                untested += 1
                continue
            variants += [(f"{name}|{label}", vmod, export, eargs, rtype) for label, vmod in vs]

        def _route(v):                                         # assemble + validate (parallel) to classify
            _wp, wsm, valid = prepare(v[1])
            return v + (wsm, valid)
        routed = pool("routing (assemble + validate variants)", _route, variants)

        exec_cases, invalid_segs = [], []
        for nm, vmod, export, eargs, rtype, wsm, valid in routed:
            if wsm is None:
                continue                                       # malformed mutation — didn't assemble
            if valid:
                exec_cases.append((nm, vmod, export, eargs, None, rtype))
            else:
                invalid_segs.append({"name": nm, "module": vmod, "kind": "invalid",
                                     "reason": "mutated ill-typed", "stateful": False, "actions": []})
        print(f"# mode=mutate  files={stats['files']}  modules={stats['modules']}  "
              f"variants: valid={len(exec_cases)}  ill-typed={len(invalid_segs)}  (no-op seeds={untested})")
        print(f"# engines={'+'.join(base_cols)}  sut={sut}  jobs={args.jobs}")
        print(f"# tools={tool_versions(engines)}", flush=True)
        if exec_cases:
            section("execution — mutated VALID variants (value / trap differential)",
                    pool("execution", lambda c: differential(c, sut, engines), exec_cases), base_cols)
        if invalid_segs:
            section("validation — mutated ILL-TYPED variants: does the SUT reject them?",
                    pool("validation", lambda s: validation_differential(s, sut, engines), invalid_segs),
                    ["wtools"] + base_cols)
    else:
        cases, stats = load_corpus(args.seeds)
        work, untested = ([], 0) if args.mode == "invalid" else MODES[args.mode](cases, args.n)
        inval = invalid_battery() if args.mode in ("invalid", "all") else []
        print(f"# mode={args.mode}  files={stats['files']}  modules={stats['modules']}  cases={len(work)}"
              + (f"  invalid={len(inval)}" if inval else ""))
        print(f"# engines={'+'.join(base_cols)}  sut={sut}  jobs={args.jobs}")
        print(f"# tools={tool_versions(engines)}", flush=True)
        if work:
            section("execution — per-action value / trap differential",
                    pool("execution", lambda c: differential(c, sut, engines), work), base_cols)
        if inval:
            section("validation — spec-invalid GC modules: does the SUT reject them?",
                    pool("validation", lambda s: validation_differential(s, sut, engines), inval),
                    ["wtools"] + base_cols)

    findings = classes["SOUNDNESS"] + classes["VALUE"]
    print("\n" + "=" * 70)
    print(f"FINDINGS={findings}  SOUNDNESS={classes['SOUNDNESS']}  VALUE={classes['VALUE']}")
    print("  by class: " + "  ".join(f"{k}={classes[k]}" for k in CLASS_ORDER if classes[k]))
    if log:
        with open(os.path.join(WORK, "divergences.txt"), "w") as f:
            f.write("\n".join(log) + "\n")
        body, total, distinct = dedupe_summary(records)
        print(f"# {total} finding(s) in {distinct} distinct bug(s) "
              f"(base case x mutation labels collapsed) -> {os.path.join(WORK, 'divergences.txt')}")
        print(body)
        print(f"# reproducers   -> {os.path.join(WORK, 'repro')}/  ({len(repro_dirs)} dir(s))")
