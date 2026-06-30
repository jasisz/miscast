"""Run one case / segment through every detected engine and classify the result."""
from .toolchain import prepare
import os

from .engines import ENGINES, validate_module, conformance_file, first_export
from .verdict import classify, classify_validation, classify_conformance

SELFCHECK_PREFIXES = ("morphism", "recgroup", "compose", "trapline", "memory64",
                      "arrayops", "callref", "castalgebra", "constinit")


def differential(case, sut, engines=ENGINES):
    """Per-action value/trap differential (a non-stateful run-segment action, or a bare .wat)."""
    name, module_wat, export, args, expected, rtype = case
    wp, wsm, valid = prepare(module_wat)
    repro = {"wat": module_wat, "export": export, "args": args, "rtype": rtype,
             "wat_path": wp, "wasm_path": wsm}
    if wsm is None:
        return name, {}, expected, "assemble-fail", False, repro
    if not valid:
        return name, {}, expected, "invalid", False, repro
    verdicts = {en: fn(wp, wsm, export, args) for en, fn in engines.items()}
    expected_is_oracle = bool(expected) and name.startswith(SELFCHECK_PREFIXES)
    verdict, isdiv = classify(verdicts, sut, expected, rtype, expected_is_oracle)
    return name, verdicts, expected, verdict, isdiv, repro


def validation_differential(segment, sut, engines=ENGINES):
    """Does every engine REJECT this invalid module? SUT accepting/running it = unsound."""
    verdicts, wp, wsm = validate_module(segment["module"], list(engines))
    verdict, isfind = classify_validation(verdicts, sut)
    repro = {"wat": segment["module"], "export": first_export(segment["module"]) or "(none)",
             "args": [], "rtype": None, "wat_path": wp, "wasm_path": wsm,
             "kind": "validation", "reason": segment["reason"]}
    return segment["name"], verdicts, "invalid", verdict, isfind, repro


def conformance_differential(group, sut, engines=ENGINES):
    """Stateful .wast conformance: run a whole official file natively; SUT must agree with oracles.
    `group` = {name, src, nstateful} — one entry per file that contains stateful segments."""
    verdicts = conformance_file(group["src"], list(engines))
    verdict, isfind = classify_conformance(verdicts, sut)
    repro = {"wat": "", "export": "(script)", "args": [], "rtype": None,
             "wat_path": None, "wasm_path": None, "kind": "conformance",
             "src": group["src"], "nstateful": group["nstateful"]}
    return group["name"], verdicts, "script", verdict, isfind, repro
