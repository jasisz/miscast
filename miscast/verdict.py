"""Classify the SUT's result against the agreeing oracle pool.

Pure functions over the normalized verdict strings the engine backends produce
("OK <raw>" | "OK _" | "OK ref" | "TRAP" | "UNSUP" | ...). The value is compared
with the key appropriate to the export's result type: integers as u32, floats by
their numeric value (NaN canonicalized), and references / void by status only.
Returns (verdict_label, is_divergence).
"""


def _status(v):
    s = v.split()[0]
    return "OK" if s in ("OK", "RET") else s


def _ikey(v, bits=32):
    """integer comparison key masked to `bits` — engines print i32/i64 with differing sign
    conventions (e.g. 4294967295 vs -1), so normalize by result width. i64 MUST use 64 bits or
    a divergence only in the high 32 bits is invisible. None if it isn't a plain integer."""
    p = v.split()
    if p[0] != "OK" or len(p) != 2 or p[1] in ("_", "ref"):
        return None
    try:
        return int(p[1], 0) & ((1 << bits) - 1)
    except ValueError:
        return None


def _fkey(v):
    """numeric comparison key for a float result (any NaN -> one key), or None."""
    p = v.split(None, 1)
    if p[0] != "OK" or len(p) != 2:
        return None
    raw = p[1].strip().lower()
    if raw in ("_", "ref"):
        return None
    if "nan" in raw:
        return "nan"                          # many NaN bit-patterns are spec-legal -> canonicalize
    try:
        # compare at fixed 6-decimal precision: engines print floats differently (sci vs full vs
        # truncated %f), so an exact text/value compare is noise. This is the floor the lossiest
        # printer (e.g. an interpreter's %f) can show — it catches gross value bugs, not sub-ULP.
        return f"{float(raw):.6f}"
    except ValueError:
        return None


def _floatbits_key(v, bits):
    """Comparison key for a float result reinterpreted to its integer bits (see reify.bitcast_float_result):
    an EXACT bit compare that catches sub-ULP miscompiles the 6-decimal _fkey hides — but any NaN
    bit-pattern collapses to one key, since the spec leaves a NaN's payload nondeterministic. None if the
    value isn't a plain integer."""
    p = v.split()
    if p[0] != "OK" or len(p) != 2 or p[1] in ("_", "ref"):
        return None
    try:
        n = int(p[1], 0) & ((1 << bits) - 1)
    except ValueError:
        return None
    exp = 0x7F800000 if bits == 32 else 0x7FF0000000000000
    man = 0x7FFFFF if bits == 32 else 0xFFFFFFFFFFFFF
    if (n & exp) == exp and (n & man) != 0:
        return "nan"                                       # spec-nondeterministic NaN payload
    return n


def classify_validation(verdicts, sut):
    """Validation-differential: every oracle REJECTs an invalid module; the SUT must too.
    SUT ACCEPT where the oracles agree REJECT = the module ran/loaded unsound."""
    ss = verdicts.get(sut)
    oracles = [v for e, v in verdicts.items() if e != sut and v in ("REJECT", "ACCEPT")]
    if not oracles:
        return "oracle-unsup", False
    if any(v == "ACCEPT" for v in oracles):
        return "oracle-split", False                # an oracle thinks it's valid -> confounder
    if ss == "ACCEPT":
        return "SOUNDNESS", True                     # SUT accepted/ran a module every oracle rejects
    if ss == "REJECT":
        return "agree", False
    return "sut-unsup", False


def classify_conformance(verdicts, sut):
    """Stateful .wast conformance: the oracles run the real script and agree it PASSes its own
    asserts; the SUT must too. SUT FAIL where oracles PASS = a stateful-execution divergence."""
    ss = verdicts.get(sut, "n/a")
    oracles = [v for e, v in verdicts.items() if e != sut and v in ("PASS", "FAIL")]
    if not oracles:
        return "oracle-unsup", False
    if any(v == "FAIL" for v in oracles):
        return "oracle-split", False                # oracles disagree with the .wast's own asserts
    if ss == "FAIL":
        return "SOUNDNESS", True
    if ss == "PASS":
        return "agree", False
    return "sut-stateful-na", False                  # one-shot SUT can't execute a stateful script


def classify(verdicts, sut, expected, rtype="int"):
    if sut not in verdicts:
        return "no-sut", False
    s = verdicts[sut]
    if s == "SUT_NA":
        return "sut-na", False                       # SUT couldn't receive this action's args — skip, not a finding
    # The production engines are ground truth. The .wast assert is only a FALLBACK oracle, used
    # when no live engine can run the case — never folded in alongside the engines, because a
    # stateful test or a parse quirk can make it disagree with the engines on our fresh-per-invoke
    # run; trusting the engines avoids that poisoning the consensus.
    engines = [v for k, v in verdicts.items() if k != sut]
    runnable = [v for v in engines if _status(v) in ("OK", "TRAP")]
    if not runnable and expected and _status(expected) in ("OK", "TRAP"):
        runnable = [expected]
    if not runnable:
        return "oracle-unsup", False
    statuses = {_status(v) for v in runnable}
    if len(statuses) > 1:
        return "oracle-split", False                # oracles disagree on trap-vs-return -> confounder
    ostatus = statuses.pop()
    ss = _status(s)
    if ss == "CRASH":
        return "CRASH", True                         # the engine fell over on a module the oracles ran — robustness
    if ss not in ("OK", "TRAP"):
        # we are past the oracle-unsup gate, so the oracles agreed on a result (OK or TRAP) the SUT
        # couldn't produce — it errored / refused a *valid* module the engines handled. That is a
        # completeness-family defect (over-rejection), not soundness; flagged only with --overtrap.
        return "sut-reject", True
    if ostatus == "TRAP" and ss == "OK":
        return "SOUNDNESS", True
    if ostatus == "OK" and ss == "TRAP":
        return "completeness", True
    if ostatus == "OK" and ss == "OK":
        # compare the value with the key for this result type; a ref / void / unknown result
        # (rtype None) is compared by status only.
        keyfn = ((lambda v: _ikey(v, 32)) if rtype == "int"
                 else (lambda v: _ikey(v, 64)) if rtype == "int64"
                 else (lambda v: _floatbits_key(v, 32)) if rtype == "f32bits"
                 else (lambda v: _floatbits_key(v, 64)) if rtype == "f64bits"
                 else _fkey if rtype == "float" else None)
        if keyfn:
            ovals = {keyfn(v) for v in runnable if keyfn(v) is not None}
            sval = keyfn(s)
            if len(ovals) > 1:
                return "oracle-split", False
            if ovals and sval is not None and sval != next(iter(ovals)):
                return "VALUE", True
    return "agree", False
