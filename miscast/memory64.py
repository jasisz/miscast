"""memory64 address-truncation / sandbox escape: a 64-bit linear-memory address that is out of bounds must
TRAP — it must not be truncated to its low 32 bits and wrapped back in.

This is a memory-safety class, not a wrong number: if an engine drops the high 32 bits of a memory64 address
before its bounds check, then an address `2^32 + k` aliases offset `k` and a load / store there reads / writes
memory the program can't legally reach, with no trap. Each program plants a distinguished sentinel at a low,
in-bounds offset, then accesses a high address that (in a correct engine) is out of bounds — so the baked
oracle is `TRAP`, and an engine that returns the sentinel is **proving** it truncated the address (a SUT
running where every oracle traps is the SOUNDNESS class). A few in-bounds controls confirm the engine works
normally; an in-32-bit OOB control confirms the ordinary bounds check still fires (the bug is specifically the
dropped HIGH bits).

No second engine required — the spec fixes the verdict (a high address is OOB → trap).
"""

_SENT = 0x55555555                                       # 1431655765 — a witness an alias hands straight back
_HIGH = [4294967296, 4294967304, 8589934592, 1099511627776, 4295032832]   # 2^32, 2^32+8, 2^33, 2^40, 2^32+0x10000


def _prog(body):
    return f'(module (memory i64 1)\n  (func (export "f") (result i32)\n    {body}))'


def memory64_gen(seed):
    """Return (label, export, expected, wat): a memory64 high-address probe."""
    s = seed % 6
    hi = _HIGH[(seed // 6) % len(_HIGH)]
    if s == 0:                                           # load alias: store sentinel low, load high -> TRAP
        k = hi - 4294967296                              # the truncated offset this high address aliases to
        return (f"load-alias[hi={hi:#x}]", "f", "TRAP",
                _prog(f"(i32.store (i64.const {k}) (i32.const {_SENT}))\n    (i32.load (i64.const {hi}))"))
    if s == 1:                                           # store escape: store at high, read the aliased low -> TRAP
        k = hi - 4294967296
        return (f"store-escape[hi={hi:#x}]", "f", "TRAP",
                _prog(f"(i32.store (i64.const {hi}) (i32.const {_SENT}))\n    (i32.load (i64.const {k}))"))
    if s == 2:                                           # pure high load, no aliasing target -> TRAP
        return (f"high-load[hi={hi:#x}]", "f", "TRAP", _prog(f"(i32.load (i64.const {hi}))"))
    if s == 3:                                           # narrow access at a high address -> TRAP
        return (f"high-load8[hi={hi:#x}]", "f", "TRAP", _prog(f"(i32.load8_u (i64.const {hi}))"))
    if s == 4:                                           # control: an in-bounds round trip must work
        return ("control-inbounds", "f", f"OK {_SENT}",
                _prog(f"(i32.store (i64.const 16) (i32.const {_SENT}))\n    (i32.load (i64.const 16))"))
    # control: an OOB address that is < 2^32 must still trap (isolates the bug to the dropped HIGH bits)
    return ("control-oob32", "f", "TRAP", _prog("(i32.load (i64.const 131072))"))


if __name__ == "__main__":
    import subprocess, os
    os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("WASMEDGE_LIB", os.path.expanduser("~/wasm-engines/wasmedge/lib"))
    ENG = os.path.expanduser("~/wasm-engines")
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WT = f"{ENG}/wasmtime-v46/wasmtime"; WE = f"{ENG}/wasmedge/bin/wasmedge"
    MCR = f"{REPO}/runner/target/release/mc-runner"
    NODE = os.path.expanduser("~/.nvm/versions/node/v26.3.0/bin/node"); V8 = f"{REPO}/miscast/oracle/v8.js"

    import re

    def res(p):
        out = ((p.stdout or "") + (p.stderr or "")).lower()
        if p.returncode != 0 or any(k in out for k in (
                "trap", "out of bounds", "overflow", "unreachable", "execution failed", "runtimeerror")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "memory64=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== memory64: a high (>= 2^32) address must trap — an engine that returns the sentinel truncated it ===")
    bad = 0
    for s in range(30):
        label, export, expected, wat = memory64_gen(s)
        open("/tmp/m64.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/m64.wat", "-o", "/tmp/m64.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/m64.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:26} exp={expected:12} {row}")
    print(f"\n30 programs, {bad} disagreeing")
