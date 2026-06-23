"""Run one case through every detected engine and classify the result."""
from .toolchain import prepare
from .engines import ENGINES
from .verdict import classify


def differential(case, sut, engines=ENGINES):
    name, module_wat, export, args, expected, rtype = case
    wp, wsm, valid = prepare(module_wat)
    repro = {"wat": module_wat, "export": export, "args": args, "rtype": rtype,
             "wat_path": wp, "wasm_path": wsm}
    if wsm is None:
        return name, {}, expected, "assemble-fail", False, repro
    if not valid:
        return name, {}, expected, "invalid", False, repro
    verdicts = {en: fn(wp, wsm, export, args) for en, fn in engines.items()}
    verdict, isdiv = classify(verdicts, sut, expected, rtype)
    return name, verdicts, expected, verdict, isdiv, repro
