"""Write a self-contained, minimized reproducer for a divergence — one of three kinds:
execution (per-action value/trap), validation (assert_invalid), conformance (stateful .wast)."""
import os
import re

from .config import WORK, SPEC_WASM, NODE, ORACLE, FEATURES
from .engines import repro_command


def _slug(name):
    return re.sub(r"[^\w.-]+", "_", name)[:80]


def _dir(name):
    d = os.path.join(WORK, "repro", _slug(name))
    os.makedirs(d, exist_ok=True)
    return d


def write_repro(name, repro, verdicts, expected, cols):
    kind = repro.get("kind", "execution")
    if kind == "validation":
        return _write_validation(name, repro, verdicts, cols)
    if kind == "conformance":
        return _write_conformance(name, repro, verdicts, cols)
    return _write_execution(name, repro, verdicts, expected, cols)


def _write_execution(name, repro, verdicts, expected, cols):
    d = _dir(name)
    export, args = repro["export"], repro["args"]
    inv = f'(invoke "{export}"' + "".join(f" ({t}.const {v})" for t, v in args) + ")"
    with open(os.path.join(d, "module.wat"), "w") as f:
        f.write(repro["wat"] + "\n")
    wast = os.path.join(d, "case.wast")
    with open(wast, "w") as f:
        f.write(repro["wat"] + "\n" + inv + "\n")
    lines = [f"# {name}  [execution / per-action value differential]",
             f"export: {export}",
             f"args:   {' '.join(t + '.const ' + v for t, v in args) or '(none)'}",
             f"assert: {expected or '(none)'}", "", "verdicts:"]
    lines += [f"  {c:10} {verdicts.get(c, '-')}" for c in cols]
    lines += ["", "commands:"]
    lines += [f"  {c:10} {repro_command(c, repro['wat_path'], repro['wasm_path'], export, args, wast)}"
              for c in cols]
    with open(os.path.join(d, "repro.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return d


def _write_validation(name, repro, verdicts, cols):
    d = _dir(name)
    with open(os.path.join(d, "module.wat"), "w") as f:
        f.write(repro["wat"] + "\n")
    ex, wp, wsm = repro["export"], repro.get("wat_path") or "module.wat", repro.get("wasm_path") or "module.wasm"
    cmds = {
        "wtools": f"wasm-tools validate {wsm} --features={FEATURES}        # nonzero rc = REJECT",
        "spec": f"{SPEC_WASM or '$SPEC_WASM'} module.wast                  # 'validation error' = REJECT",
        "wasmtime": f"wasmtime compile -W function-references=y,gc=y {wsm} -o /dev/null   # nonzero rc = REJECT",
        "v8": f"{NODE or 'node'} {ORACLE} {wsm} __validate__",
        "custom": os.environ.get("CUSTOM_CMD", "$CUSTOM_CMD").format(wat=wp, wasm=wsm, export=ex),
    }
    allcols = ["wtools"] + [c for c in cols if c != "wtools"]
    lines = [f"# {name}  [validation-differential: the module is INVALID — the SUT must reject it]",
             f"reason: {repro.get('reason') or '(spec marks it invalid)'}",
             f"export: {ex}", "", "verdicts (REJECT = correct / ACCEPT = unsound):"]
    lines += [f"  {c:10} {verdicts.get(c, '-')}" for c in allcols]
    lines += ["", "commands:"]
    lines += [f"  {c:10} {cmds[c]}" for c in allcols if c in cmds]
    with open(os.path.join(d, "repro.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return d


def _write_conformance(name, repro, verdicts, cols):
    d = _dir(name)
    src = repro.get("src", "<file>.wast")
    lines = [f"# {name}  [stateful conformance: the whole official .wast file, run natively in order]",
             f"file:     {src}",
             f"stateful: {repro.get('nstateful', '?')} segment(s)",
             "", "verdicts (PASS = the engine conforms to the file's own asserts):"]
    lines += [f"  {c:10} {verdicts.get(c, '-')}" for c in cols]
    lines += ["", "commands:",
              f"  spec       {SPEC_WASM or '$SPEC_WASM'} {src}",
              f"  wasmtime   wasmtime wast -W function-references=y,gc=y {src}"]
    with open(os.path.join(d, "repro.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return d
