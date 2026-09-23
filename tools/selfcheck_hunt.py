"""Run a self-checking generator (expected result baked in) against wasmtime binaries under many configs.

usage: python3 tools/selfcheck_hunt.py MODULE:FUNC START COUNT [--wt BIN ...] [--cfg 'flags' ...] [-j N]
Any mismatch with the model is printed and the module saved to OUTDIR (default work/selfcheck-hits)."""
import argparse, concurrent.futures as cf, importlib, os, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_CFGS = [
    "-C collector=drc",
    "-C collector=copying",
    "-C collector=null",
    "-C collector=drc -C inlining=y",
    "-C collector=copying -C inlining=y -O opt-level=2",
    "-C collector=drc -O opt-level=0",
]


def run(wt, cfg, wasm, export):
    cmd = [wt, "run", "-W", "gc=y,function-references=y,exceptions=y", *cfg.split(), "--invoke", export, wasm]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    if p.returncode == 0:
        return "OK " + p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "OK"
    err = (p.stderr.strip().splitlines() or ["?"])
    return f"ERR rc={p.returncode} " + " | ".join(l for l in err if "error" in l.lower() or "trap" in l.lower() or "panic" in l.lower())[:300]


def one(args, gen, i, tmp):
    label, export, expected, wat = gen(i)
    base = os.path.join(tmp, f"c{i}")
    open(base + ".wat", "w").write(wat)
    p = subprocess.run(["wasm-tools", "parse", base + ".wat", "-o", base + ".wasm"], capture_output=True, text=True)
    bad = []
    if p.returncode:
        bad.append(("ASSEMBLE", p.stderr[:300]))
    else:
        for wt in args.wt:
            for cfg in args.cfg:
                got = run(wt, cfg, base + ".wasm", export)
                if got != expected:
                    bad.append((f"{os.path.basename(os.path.dirname(os.path.dirname(wt)))}:{cfg}", got))
    for ext in (".wat", ".wasm"):
        if os.path.exists(base + ext):
            os.remove(base + ext)
    if bad:
        os.makedirs(args.out, exist_ok=True)
        open(os.path.join(args.out, f"{args.gen.split(':')[0].split('.')[-1]}{i}.wat"), "w").write(wat)
    return i, label, expected, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gen")
    ap.add_argument("start", type=int)
    ap.add_argument("count", type=int)
    ap.add_argument("--wt", action="append")
    ap.add_argument("--cfg", action="append")
    ap.add_argument("-j", type=int, default=8)
    ap.add_argument("--out", default="work/selfcheck-hits")
    a = ap.parse_args()
    a.cfg = a.cfg or DEFAULT_CFGS
    a.wt = a.wt or [shutil.which("wasmtime") or sys.exit("no --wt given and no wasmtime on PATH")]
    mod, fn = a.gen.split(":")
    gen = getattr(importlib.import_module(mod), fn)
    tmp = tempfile.mkdtemp(prefix="sch")
    nbad = 0
    with cf.ThreadPoolExecutor(a.j) as ex:
        for i, label, exp, bad in ex.map(lambda i: one(a, gen, i, tmp), range(a.start, a.start + a.count)):
            if bad:
                nbad += 1
                print(f"[{i}] {label} expected {exp}", flush=True)
                for c, g in bad:
                    print(f"     {c}: {g}", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"done {a.count} cases, {nbad} mismatching", flush=True)


if __name__ == "__main__":
    main()
