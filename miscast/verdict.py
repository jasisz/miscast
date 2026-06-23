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


def _ikey(v):
    """u32 comparison key for an integer result, or None if it isn't a plain integer."""
    p = v.split()
    if p[0] != "OK" or len(p) != 2 or p[1] in ("_", "ref"):
        return None
    try:
        return int(p[1], 0) & 0xFFFFFFFF
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


def classify(verdicts, sut, expected, rtype="int"):
    if sut not in verdicts:
        return "no-sut", False
    s = verdicts[sut]
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
        keyfn = _ikey if rtype == "int" else _fkey if rtype == "float" else None
        if keyfn:
            ovals = {keyfn(v) for v in runnable if keyfn(v) is not None}
            sval = keyfn(s)
            if len(ovals) > 1:
                return "oracle-split", False
            if ovals and sval is not None and sval != next(iter(ovals)):
                return "VALUE", True
    return "agree", False
