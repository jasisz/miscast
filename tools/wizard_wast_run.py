#!/usr/bin/env python3
"""Run generated text .wast scripts with Wizard.

Wizard's spectest runner accepts `.wast` scripts whose modules are written as
`(module binary "...")`, but the generated miscast scripts are ordinary text
modules. This adapter converts top-level text modules to binary-module commands
and then runs the whole script through Wizard, preserving register/invoke order.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miscast.wast import find_subform, top_forms


WIZARD = os.environ.get("WIZARD_BIN", os.path.expanduser("~/wasm-engines/wizard/wizeng"))
WASM_TOOLS = os.environ.get("WASM_TOOLS", "wasm-tools")
TIMEOUT = int(os.environ.get("WIZARD_WAST_TIMEOUT", os.environ.get("WIZ_TIMEOUT", "30")))


def _module_id(form):
    m = re.match(r"\(module(?:\s+(\$[^\s()]+))?", form)
    return m.group(1) if m and m.group(1) else None


def _binary_strings(data):
    lines = []
    for i in range(0, len(data), 16):
        chunk = "".join(f"\\{b:02x}" for b in data[i:i + 16])
        lines.append(f'  "{chunk}"')
    return "\n".join(lines)


def _convert_module_form(form, tmp, idx):
    wat = tmp / f"{idx}.wat"
    wasm = tmp / f"{idx}.wasm"
    wat.write_text(form + "\n")
    p = subprocess.run([WASM_TOOLS, "parse", str(wat), "-o", str(wasm)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stdout)
        sys.stderr.write(p.stderr)
        return None
    mid = _module_id(form.lstrip())
    head = f"(module {mid} binary" if mid else "(module binary"
    return f"{head}\n{_binary_strings(wasm.read_bytes())}\n)"


def convert_text_modules(src, dst):
    text = Path(src).read_text()
    forms = top_forms(text)
    converted = []
    with tempfile.TemporaryDirectory(prefix="miscast-wizard-wast-") as tmp:
        tmp = Path(tmp)
        for idx, form in enumerate(forms):
            stripped = form.lstrip()
            if (stripped.startswith("(module")
                    and not stripped.startswith("(module binary")
                    and not stripped.startswith("(module quote")):
                bin_form = _convert_module_form(form, tmp, idx)
                if bin_form is None:
                    return 1
                converted.append(bin_form)
            elif stripped.startswith(("(assert_unlink", "(assert_invalid", "(assert_malformed")):
                mod = find_subform(form, "module")
                if mod and not mod.lstrip().startswith(("(module binary", "(module quote")):
                    bin_form = _convert_module_form(mod, tmp, idx)
                    if bin_form is None:
                        return 1
                    converted.append(form.replace(mod, bin_form, 1))
                else:
                    converted.append(form)
            else:
                converted.append(form)
    Path(dst).write_text("\n".join(converted) + "\n")
    return 0


def main():
    if len(sys.argv) != 2:
        print("usage: wizard_wast_run.py <script.wast>", file=sys.stderr)
        return 2
    src = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="miscast-wizard-wast-") as tmp:
        dst = os.path.join(tmp, Path(src).with_suffix(".bin.wast").name)
        rc = convert_text_modules(src, dst)
        if rc != 0:
            return rc
        try:
            p = subprocess.run([WIZARD, dst], capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            print("miscast: wizard wast timeout", file=sys.stderr)
            return 124
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
