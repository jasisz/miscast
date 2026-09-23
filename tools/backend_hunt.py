"""Backend differential hunt: generate multi-export modules, run each through `wtdiff` (one wasmtime
build, several compilation strategies), keep any module whose exports disagree.

usage: python3 tools/backend_hunt.py MODULE:FUNC START COUNT [--cfgs cl2,cl0,winch,pulley] [-j N] [--out DIR]
                                     [--oracle MODULE:FUNC]
MODULE:FUNC(seed) -> WAT text. With --oracle, MODULE:FUNC(wat) -> {export: signed i64} is an independent
reference: every configuration's result must also equal it, so a bug shared by all backends is caught too."""
import argparse, concurrent.futures as cf, importlib, os, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WTDIFF = os.environ.get("WTDIFF", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                "work/wtdiff/target/release/wtdiff"))


def one(a, gen, seed, tmp):
    wat = gen(seed)
    path = os.path.join(tmp, f"m{seed}.wat")
    open(path, "w").write(wat)
    cmd = [WTDIFF, path, "--cfgs", a.cfgs] + (["--fuel", str(a.fuel)] if a.fuel else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
        stdout, stderr = p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        p, stdout, stderr = None, "", ""
    finally:
        os.remove(path)
    if p is None:
        status = "TIMEOUT"
    elif p.returncode not in (0, 3):
        tail = (stderr.strip().splitlines() or ["?"])[-3:]
        status = f"CRASH rc={p.returncode} " + " | ".join(tail)
    else:
        status = "DIFF" if p.returncode == 3 else "SAME"
    diffs = [l for l in stdout.splitlines() if l.startswith("DIFF")]
    if a.oracle_fn and p is not None and p.returncode in (0, 3):
        want = a.oracle_fn(wat)
        for l in stdout.splitlines():
            parts = l.removeprefix("DIFF ").split("\t")
            if len(parts) == 3 and parts[1] in want and parts[2] != f"i64:{want[parts[1]]}":
                diffs.append(f"ORACLE {parts[0]} {parts[1]}: got {parts[2]}, model i64:{want[parts[1]]}")
        if any(d.startswith("ORACLE") for d in diffs):
            status = "ORACLE" if status == "SAME" else status + "+ORACLE"
    if status != "SAME":
        os.makedirs(a.out, exist_ok=True)
        open(os.path.join(a.out, f"{a.tag}{seed}.wat"), "w").write(wat)
        open(os.path.join(a.out, f"{a.tag}{seed}.txt"), "w").write(
            stdout + "\n".join(d for d in diffs if d.startswith("ORACLE")) + "\n--stderr--\n" + stderr[-4000:])
    return seed, status, diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gen")
    ap.add_argument("start", type=int)
    ap.add_argument("count", type=int)
    ap.add_argument("--cfgs", default="cl2,cl0,winch,pulley")
    ap.add_argument("--fuel", type=int)
    ap.add_argument("-j", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default="work/backend-hits")
    ap.add_argument("--tag", default="")
    ap.add_argument("--oracle", help="MODULE:FUNC(wat) -> {export: expected signed i64}")
    a = ap.parse_args()
    mod, fn = a.gen.split(":")
    a.tag = a.tag or fn
    gen = getattr(importlib.import_module(mod), fn)
    a.oracle_fn = None
    if a.oracle:
        om, of = a.oracle.split(":")
        a.oracle_fn = getattr(importlib.import_module(om), of)
    tmp = tempfile.mkdtemp(prefix="bh")
    # an unsupported strategy/modifier combination (e.g. winch+nosig) would flag every seed: refuse up front
    probe = os.path.join(tmp, "probe.wat")
    open(probe, "w").write('(module (func (export "p") (result i32) (i32.const 1)))')
    p = subprocess.run([WTDIFF, probe, "--cfgs", a.cfgs], capture_output=True, text=True)
    broken = [l for l in p.stdout.splitlines() if "\t<config>\t" in l or "\t<engine>\t" in l]
    if p.returncode not in (0, 3) or broken:
        sys.exit("bad --cfgs:\n" + "\n".join(broken or [p.stderr.strip()]))
    os.remove(probe)
    bad = 0
    with cf.ThreadPoolExecutor(a.j) as ex:
        for seed, status, diffs in ex.map(lambda s: one(a, gen, s, tmp), range(a.start, a.start + a.count)):
            if status != "SAME":
                bad += 1
                print(f"[{seed}] {status}", flush=True)
                for d in diffs[:6]:
                    print("    " + d[:200], flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"done {a.count}, {bad} flagged", flush=True)


if __name__ == "__main__":
    main()
