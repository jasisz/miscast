"""Run one case through every detected engine and classify the result."""
from .toolchain import prepare
from .engines import ENGINES
from .verdict import classify


def differential(case, sut, engines=ENGINES):
    name, module_wat, export, args, expected, rtype = case
    wp, wsm, valid = prepare(module_wat)
    if wsm is None:
        return name, {}, expected, "assemble-fail", False
    if not valid:
        return name, {}, expected, "invalid", False
    verdicts = {en: fn(wp, wsm, export, args) for en, fn in engines.items()}
    verdict, isdiv = classify(verdicts, sut, expected, rtype)
    return name, verdicts, expected, verdict, isdiv
