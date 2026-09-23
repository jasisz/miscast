"""Model hunt: run generated multi-export modules on other engines and check every export against an
independent model (e.g. intalg + watmodel), so no second engine is needed to call a result wrong.

usage: python3 tools/model_hunt.py GEN_MODULE:FUNC ORACLE_MODULE:FUNC START COUNT --engine NAME [--engine ...]
                                   [-j N] [--out DIR]
GEN(seed) -> WAT; ORACLE(wat) -> {export: signed i64 | "TRAP"}. Engines are presets below; every run goes through the
~/wasm-engines/safehunt/caprun watchdog (wall clock + RSS cap), and WasmEdge through its capped `we` wrapper.
"""
import argparse, concurrent.futures as cf, importlib, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ENG = os.environ.get("WASM_ENGINES", os.path.expanduser("~/wasm-engines"))
CAP = [os.path.join(ENG, "safehunt", "caprun")]
WE = os.path.join(ENG, "safehunt", "we")
WASMEDGE = os.path.join(ENG, "wasmedge", "bin", "wasmedge")


def _iwasm(build):
    return os.path.join(ROOT, "work", f"wamr-build-{build}-asan", "iwasm")


# name -> (optional compile step(wasm, out) -> argv, run(artifact, export) -> argv)
ENGINES = {
    "wasmedge": (None, lambda w, e: [WE, "--reactor", w, e]),
    "wasmedge-aot": (lambda w, o: CAP + [WASMEDGE, "compile", "--optimize", "3", w, o],
                     lambda w, e: [WE, "--reactor", w, e]),
    "wasmedge-aot0": (lambda w, o: CAP + [WASMEDGE, "compile", "--optimize", "0", w, o],
                      lambda w, e: [WE, "--reactor", w, e]),
    # iwasm otherwise appends its own app heap to linear memory: memory.size grows by it and a program that
    # writes its whole memory corrupts WAMR's heap ("heap migrate failed") -- not a wasm-level bug
    "wamr-classic": (None, lambda w, e: CAP + [_iwasm("classic"), "--heap-size=0", "-f", e, w]),
    "wamr-fast": (None, lambda w, e: CAP + [_iwasm("fast"), "--heap-size=0", "-f", e, w]),
    "wizard": (None, lambda w, e: CAP + [os.path.join(ENG, "wizard", "wizeng"), f"--invoke={e}", "--print-result", w]),
}
# the engine lacks a feature the module uses: not a finding (a spec-invalid rejection is still reported)
_UNSUP = re.compile(r"load failed|not supported|unsupported|not enabled|not implemented|unimplemented", re.I)
# a result is a whole line: `-123`, `0xff..:i64` (WAMR) or `123uL` (Wizard). Anything else (e.g. Wizard's
# trap trace `<wasm func #4> +511`) is not a result.
_NUM = re.compile(r"^(0x[0-9a-fA-F]+)(?::i64)?$|^(-?\d+)(?:uL|L)?$")


def _signed(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


class _Out:
    def __init__(self, rc, stdout, stderr):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _run(argv, timeout, path):
    """Like subprocess.run(capture_output=True), but through files: Wizard takes ~20 s to exit when its stdout
    is a pipe (0.2 s with a file)."""
    with open(path + ".out", "w+") as o, open(path + ".err", "w+") as e:
        rc = subprocess.run(argv, stdout=o, stderr=e, stdin=subprocess.DEVNULL, timeout=timeout).returncode
        o.seek(0)
        e.seek(0)
        return _Out(rc, o.read(), e.read())


def parse(out):
    for line in reversed(out.strip().splitlines()):
        m = _NUM.search(line.strip())
        if m:
            return _signed(int(m.group(1), 16) if m.group(1) else int(m.group(2)))
    return None


def one(a, gen, oracle, seed, tmp):
    wat = gen(seed)
    base = os.path.join(tmp, f"s{seed}")
    open(base + ".wat", "w").write(wat)
    subprocess.run(["wasm-tools", "parse", base + ".wat", "-o", base + ".wasm"], check=True)
    want = oracle(wat)
    bad, unsup = [], []
    for name in a.engine:
        compile_step, run = ENGINES[name]
        art = base + ".wasm"
        if compile_step:
            art = base + f".{name}.so"
            p = _run(compile_step(base + ".wasm", art), a.timeout, base)
            if p.returncode:
                bad.append((name, "*", f"COMPILE rc={p.returncode} {(p.stderr or p.stdout).strip()[-200:]}", None))
                continue
        for export, exp in want.items():
            if name in unsup:
                break
            try:
                p = _run(run(art, export), a.timeout, base)
            except subprocess.TimeoutExpired:
                bad.append((name, export, "TIMEOUT", exp))
                continue
            if p.returncode and _UNSUP.search(p.stderr + p.stdout):
                unsup.append(name)
                continue
            got = None if "!trap" in p.stdout + p.stderr else parse(p.stdout)
            # Wizard exits with the invoked function's result as its status (the low byte of it)
            rc_ok = p.returncode == 0 or (name == "wizard" and got is not None and p.returncode == got & 0xFF)
            got = got if rc_ok else None
            if exp == "TRAP":  # the reference trapped: the engine must fail too (a capped run is not a trap)
                if rc_ok or "CAPPED" in p.stderr + p.stdout:
                    bad.append((name, export, got if rc_ok else f"rc={p.returncode} (capped)", exp))
                continue
            if got != exp:
                detail = got if got is not None else f"rc={p.returncode} {(p.stderr + p.stdout).strip()[-200:]}"
                bad.append((name, export, detail, exp))
    for f in os.listdir(tmp):
        if f.startswith(f"s{seed}."):
            os.remove(os.path.join(tmp, f))
    if bad:
        os.makedirs(a.out, exist_ok=True)
        open(os.path.join(a.out, f"{a.tag}{seed}.wat"), "w").write(wat)
    return seed, bad, unsup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gen")
    ap.add_argument("oracle")
    ap.add_argument("start", type=int)
    ap.add_argument("count", type=int)
    ap.add_argument("--engine", action="append", required=True, choices=list(ENGINES))
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--out", default="work/model-hits")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    load = lambda spec: getattr(importlib.import_module(spec.split(":")[0]), spec.split(":")[1])
    gen, oracle = load(a.gen), load(a.oracle)
    a.tag = a.tag or a.gen.split(":")[0].split(".")[-1]
    tmp = tempfile.mkdtemp(prefix="mh")
    nbad = 0
    nunsup = {}
    with cf.ThreadPoolExecutor(a.j) as ex:
        for seed, bad, unsup in ex.map(lambda s: one(a, gen, oracle, s, tmp), range(a.start, a.start + a.count)):
            for name in unsup:
                nunsup[name] = nunsup.get(name, 0) + 1
            if bad:
                nbad += 1
                print(f"[{seed}]", flush=True)
                for name, export, got, exp in bad[:8]:
                    print(f"    {name} {export}: got {got}, model {exp}", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    extra = ", unsupported: " + ", ".join(f"{k} {v}" for k, v in sorted(nunsup.items())) if nunsup else ""
    print(f"done {a.count}, {nbad} flagged{extra}", flush=True)


if __name__ == "__main__":
    main()
