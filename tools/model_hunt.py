"""Model hunt: run generated multi-export modules on other engines and check every export against an
independent model (e.g. intalg + watmodel), so no second engine is needed to call a result wrong.

usage: python3 tools/model_hunt.py GEN_MODULE:FUNC ORACLE_MODULE:FUNC START COUNT --engine NAME [--engine ...]
                                   [-j N] [--out DIR]
GEN(seed) -> WAT; ORACLE(wat) -> {export: signed i64 | "TRAP"}. With ORACLE `self`, GEN is a self-checking
generator returning (label, export, expected, wat), e.g. miscast.gcalias:gcalias_gen. Engines are presets below; every run goes through the
~/wasm-engines/safehunt/caprun watchdog (wall clock + RSS cap), and WasmEdge through its capped `we` wrapper.
"""
import argparse, concurrent.futures as cf, importlib, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ENG = os.environ.get("WASM_ENGINES", os.path.expanduser("~/wasm-engines"))
CAP = [os.path.join(ENG, "safehunt", "caprun")]
WE = os.path.join(ENG, "safehunt", "we")
WASMEDGE = os.path.join(ENG, "wasmedge", "bin", "wasmedge")
# freshest builds (see ~/wasm-engines/build): hunt on these so fixed bugs are not re-found
WASMEDGE_MAIN = os.path.join(ENG, "build", "wasmedge-main", "build", "tools", "wasmedge", "wasmedge")
WIZARD_MAIN_JAR = os.path.join(ENG, "build", "wizard-main", "bin", "wizeng.jvm.jar")
_WE_CAPS = ["--memory-page-limit", "2048", "--time-limit", "10000"]
WASM3_MAIN = os.path.join(ENG, "build", "wasm3-main", "build", "wasm3")
WASMER_MAIN = os.path.join(ENG, "build", "wasmer-main", "target", "release", "wasmer")
# no --enable-* flags: asking a backend for a feature it lacks makes wasmer refuse every module
_WASMER_FEAT = []


WAMRC = os.path.join(ROOT, "work", "wamrc-build", "wamrc")
_WAMR_GC = ["--heap-size=0", "--gc-heap-size=67108864", "--stack-size=8388608"]


def _wamrc(opt):
    return lambda w, o: CAP + [WAMRC, f"--opt-level={opt}", "--enable-gc", "--enable-tail-call", "-o", o, w]


def _endive(mode):
    """Endive main (Chicory's successor, built from ~/wasm-engines/build/endive-main) through
    ~/wasm-engines/chicory/endive/EndiveRun; mode is `interp` or `aot` (the JVM-bytecode compiler)."""
    d = os.path.join(ENG, "chicory")
    def argv(w, e):
        cp = open(os.path.join(d, "endive-classpath.txt")).read().strip() + ":" + os.path.join(d, "endive")
        return CAP + ["/opt/homebrew/opt/openjdk/bin/java", "-cp", cp, "EndiveRun", mode, w, e]
    return argv


def _wizard_main(w, e):
    return CAP + ["/opt/homebrew/opt/openjdk/bin/java", "-jar", WIZARD_MAIN_JAR, f"--invoke={e}", "--print-result", w]


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
    # GC heaps big enough that allocation-heavy GC programs are not cut short by WAMR's small default
    # and an 8 MiB wasm stack (iwasm defaults to 64 KiB, which deep but valid call chains exhaust)
    # (the classic build's fixed global pool must also back the stack: 4 MiB GC heap + 2 MiB stack fit)
    "wamr-classic": (None, lambda w, e: CAP + [_iwasm("classic"), "--heap-size=0", "--gc-heap-size=4194304",
                                               "--stack-size=2097152",
                                               "-f", e, w]),
    "wamr-fast": (None, lambda w, e: CAP + [_iwasm("fast"), "--heap-size=0", "--gc-heap-size=67108864",
                                            "--stack-size=8388608",
                                            "-f", e, w]),
    "wizard": (None, lambda w, e: CAP + [os.path.join(ENG, "wizard", "wizeng"), f"--invoke={e}", "--print-result", w]),
    "wasmedge-main": (None, lambda w, e: CAP + [WASMEDGE_MAIN, "run"] + _WE_CAPS + ["--reactor", w, e]),
    "wasmedge-main-aot": (lambda w, o: CAP + [WASMEDGE_MAIN, "compile", "--optimize", "3", w, o],
                          lambda w, e: CAP + [WASMEDGE_MAIN, "run"] + _WE_CAPS + ["--reactor", w, e]),
    "wizard-main": (None, _wizard_main),
    "wasmer-sp": (None, lambda w, e: CAP + [WASMER_MAIN, "run", "--singlepass"] + _WASMER_FEAT + ["--invoke", e, w]),
    "wasmer-cl": (None, lambda w, e: CAP + [WASMER_MAIN, "run", "--cranelift"] + _WASMER_FEAT + ["--invoke", e, w]),
    "endive": (None, _endive("interp")),
    "endive-aot": (None, _endive("aot")),
    "wasm3": (None, lambda w, e: CAP + [WASM3_MAIN, "--stack-size", "8388608", "--func", e, w]),
    "wasm3-eager": (None, lambda w, e: CAP + [WASM3_MAIN, "--compile", "--stack-size", "8388608", "--func", e, w]),
    "wasmi": (None, lambda w, e: CAP + [os.path.join(ENG, "wasmi", "bin", "wasmi"), "--compilation-mode", "eager",
                                        "--invoke", e, w]),
    "wasmi-lazy": (None, lambda w, e: CAP + [os.path.join(ENG, "wasmi", "bin", "wasmi"), "--compilation-mode", "lazy",
                                             "--invoke", e, w]),
    # WAMR AOT (wamrc, LLVM) executed by an AOT+GC iwasm built with ASan/UBSan
    "wamr-aot": (_wamrc(3), lambda w, e: CAP + [os.path.join(ROOT, "work", "wamr-build-aot-asan", "iwasm")] + _WAMR_GC
                 + ["-f", e, w]),
    "wamr-aot0": (_wamrc(0), lambda w, e: CAP + [os.path.join(ROOT, "work", "wamr-build-aot-asan", "iwasm")]
                  + _WAMR_GC + ["-f", e, w]),
}
# the engine lacks a feature the module uses: not a finding (a spec-invalid rejection is still reported)
_UNSUP = re.compile(r"load failed|loading failed: illegal opcode|not supported|unsupported|not enabled|not implemented|not yet implemented|unimplemented|"
                    r"invalid section id|does not support the required features|doesn.t recognize Instruction|Unhandled opcode", re.I)
# a result is a whole line: `-123`, `0xff..:i64` (WAMR) or `123uL` (Wizard). Anything else (e.g. Wizard's
# trap trace `<wasm func #4> +511`) is not a result.
_NUM = re.compile(r"^(0x[0-9a-fA-F]+)(?::i(?:32|64))?$|^(-?\d+)(?:uL|L)?$")


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
        line = line.strip().removeprefix("Result:").strip()  # wasm3 prints `Result: <value>`
        m = _NUM.search(line)
        if m:
            return _signed(int(m.group(1), 16) if m.group(1) else int(m.group(2)))
    return None


def _same(got, exp):
    """Equal, or equal as i32 bit patterns (an i32 result may print signed or unsigned depending on the engine)."""
    if got == exp:
        return True
    return got is not None and abs(got) < (1 << 32) and abs(exp) < (1 << 32) and (got - exp) % (1 << 32) == 0


def _baked(expected):
    """A self-checking generator's expectation ("OK <int>" / "TRAP") as a model value, or None if unusable."""
    if expected.startswith("TRAP"):
        return "TRAP"
    parts = expected.split()
    return int(parts[1]) if len(parts) == 2 and parts[0] == "OK" and re.fullmatch(r"-?\d+", parts[1]) else None


def one(a, gen, oracle, seed, tmp):
    res = gen(seed)
    if oracle is None:  # self-checking generator
        _label, export, expected, wat = res
        want = {export: _baked(expected)} if _baked(expected) is not None else {}
    else:
        wat = res
    base = os.path.join(tmp, f"s{seed}")
    open(base + ".wat", "w").write(wat)
    subprocess.run(["wasm-tools", "parse", base + ".wat", "-o", base + ".wasm"], check=True)
    if oracle is not None:
        want = oracle(wat)
    bad, unsup = [], []
    for name in a.engine:
        compile_step, run = ENGINES[name]
        art = base + ".wasm"
        if compile_step:
            art = base + f".{name}.so"
            p = _run(compile_step(base + ".wasm", art), a.timeout, base)
            if p.returncode:
                if _UNSUP.search(p.stderr + p.stdout):
                    unsup.append(name)
                else:
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
            # (some engines, e.g. wasm3, print the result on stderr)
            got = None if "!trap" in p.stdout + p.stderr else (parse(p.stdout) if p.stdout.strip() else parse(p.stderr))
            # Wizard exits with the invoked function's result as its status (the low byte of it)
            rc_ok = p.returncode == 0 or (name.startswith("wizard") and got is not None and p.returncode == got & 0xFF)
            got = got if rc_ok else None
            if exp == "TRAP":  # the reference trapped: the engine must fail too (a capped run is not a trap)
                if rc_ok or "CAPPED" in p.stderr + p.stdout:
                    bad.append((name, export, got if rc_ok else f"rc={p.returncode} (capped)", exp))
                continue
            if not _same(got, exp):
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
    gen, oracle = load(a.gen), (None if a.oracle == "self" else load(a.oracle))
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
