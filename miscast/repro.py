"""Write a self-contained, minimized reproducer for a divergence."""
import os
import re

from .config import WORK
from .engines import repro_command


def _slug(name):
    return re.sub(r"[^\w.-]+", "_", name)[:80]


def write_repro(name, repro, verdicts, expected, cols):
    """Emit work/repro/<name>/ — module.wat, case.wast (module + the invoke, runnable on the
    reference interpreter as-is), and repro.txt (per-engine verdicts + the exact command each
    engine ran). Everything needed to re-run the divergence by hand. Returns the dir path."""
    d = os.path.join(WORK, "repro", _slug(name))
    os.makedirs(d, exist_ok=True)
    export, args = repro["export"], repro["args"]
    inv = f'(invoke "{export}"' + "".join(f" ({t}.const {v})" for t, v in args) + ")"
    with open(os.path.join(d, "module.wat"), "w") as f:
        f.write(repro["wat"] + "\n")
    wast = os.path.join(d, "case.wast")
    with open(wast, "w") as f:
        f.write(repro["wat"] + "\n" + inv + "\n")
    lines = [f"# {name}",
             f"export: {export}",
             f"args:   {' '.join(t + '.const ' + v for t, v in args) or '(none)'}",
             f"assert: {expected or '(none)'}",
             "", "verdicts:"]
    lines += [f"  {c:10} {verdicts.get(c, '-')}" for c in cols]
    lines += ["", "commands:"]
    lines += [f"  {c:10} {repro_command(c, repro['wat_path'], repro['wasm_path'], export, args, wast)}"
              for c in cols]
    with open(os.path.join(d, "repro.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return d
