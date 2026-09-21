"""GC stack-map and compiler-liveness stress generator.

Most miscast modes test a Wasm semantic law.  This one targets the machinery below those laws: a compiler
must tell a moving collector about every live GC reference at every allocation/call safepoint, including
references that exist only as SSA values on the operand stack.  Losing one root can turn into a stale read,
wrong-object read, null, crash, or use-after-free.

Every generated module keeps many uniquely tagged objects alive across allocation storms and then computes
a baked checksum.  The families deliberately produce different compiler IR shapes: GC-typed locals,
partially-evaluated call arguments, multi-value returns, loop-carried block parameters, and a mixture of
traced and untraced reference representations.  ``engines.py`` recognizes the marker below and runs these
cases with aggressive GC/tiering settings in Wasmtime and V8.
"""

MARKER = "miscast-stackmap-stress"

_COUNTS = (8, 12, 16, 24, 32)
_HOT_ITERS = 24
_CHURN = 12


def _sum_expr(refs):
    expr = "(i32.const 0)"
    for i, ref in enumerate(refs):
        expr = f"(i32.add {expr} (i32.mul (struct.get $node 0 {ref}) (i32.const {i + 1})))"
    return expr


def _expected(seed, n):
    base = 1000 + seed * 97
    values = [base + i * 13 for i in range(n)]
    return base, values, sum((i + 1) * v for i, v in enumerate(values)) & 0xFFFFFFFF


def _common(extra=""):
    return f"""(module ;; {MARKER}
  (type $node (struct (field i32)))
  (type $junk (array (mut i64)))
  (global $sink (mut (ref null $junk)) (ref.null $junk))
  (func $churn (param $n i32) (result i32) (local $i i32)
    (block $done
      (loop $again
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (global.set $sink (array.new_default $junk (i32.const 256)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $again)))
    (local.get $n))
{extra}"""


def _hot_driver():
    return f"""  (func (export "f") (result i32) (local $i i32) (local $answer i32)
    (block $done
      (loop $hot
        (local.set $answer (call $probe))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $hot (i32.lt_u (local.get $i) (i32.const {_HOT_ITERS})))))
    (local.get $answer))"""


def _locals(seed, n):
    _base, values, expected = _expected(seed, n)
    decls = " ".join(f"(local $r{i} (ref $node))" for i in range(n))
    init = "\n".join(
        f"    (local.set $r{i} (struct.new $node (i32.const {value})))" for i, value in enumerate(values)
    )
    checksum = _sum_expr([f"(local.get $r{i})" for i in range(n)])
    body = f"""  (func $probe (result i32) {decls}
{init}
    (drop (call $churn (i32.const {_CHURN})))
    {checksum})
{_hot_driver()}
)"""
    return f"locals-{n}", expected, _common(body)


def _sum_func(n):
    params = " ".join(f"(param $p{i} (ref $node))" for i in range(n))
    refs = [f"(local.get $p{i})" for i in range(n)]
    return f"  (func $sum {params} (param $guard i32) (result i32)\n    (drop (local.get $guard))\n    {_sum_expr(refs)})"


def _operand(seed, n):
    _base, values, expected = _expected(seed, n)
    args = "\n".join(f"      (struct.new $node (i32.const {value}))" for value in values)
    body = f"""{_sum_func(n)}
  (func $probe (result i32)
    (call $sum
{args}
      (call $churn (i32.const {_CHURN}))))
{_hot_driver()}
)"""
    return f"operand-args-{n}", expected, _common(body)


def _multivalue(seed, n):
    _base, values, expected = _expected(seed, n)
    results = " ".join("(ref $node)" for _ in range(n))
    made = "\n".join(f"    (struct.new $node (i32.const {value}))" for value in values)
    body = f"""{_sum_func(n)}
  (func $make (result {results})
{made})
  (func $probe (result i32)
    (call $sum
      (call $make)
      (call $churn (i32.const {_CHURN}))))
{_hot_driver()}
)"""
    return f"multivalue-{n}", expected, _common(body)


def _loop_phi(seed, n):
    _base, values, expected = _expected(seed, n)
    params = " ".join("(ref $node)" for _ in range(n))
    initial = "\n".join(f"    (struct.new $node (i32.const {value}))" for value in values)
    # The references remain on the operand stack throughout the loop.  They are simultaneously the loop's
    # branch arguments and live roots across the call to $churn; no GC-typed local can accidentally save us.
    body = f"""{_sum_func(n)}
  (func $probe (result i32) (local $i i32)
{initial}
    (loop $carry (param {params}) (result {params})
      (drop (call $churn (i32.const 1)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (local.get $i)
      (i32.const {_CHURN})
      (i32.lt_u)
      (br_if $carry))
    (i32.const 0)
    (call $sum))
{_hot_driver()}
)"""
    return f"loop-phi-{n}", expected, _common(body)


def _mixed(seed, _n):
    base = 2000 + seed * 83
    vals = [base + i * 17 for i in range(6)]
    expected = (sum(vals) + 29) & 0xFFFFFFFF
    extra = f"""  (type $base (sub (struct (field i32))))
  (type $sub (sub $base (struct (field i32) (field i32))))
  (type $arr (array (mut i32)))
  (type $ft (func (result i32)))
  (func $callee (type $ft) (i32.const 29))
  (elem declare func $callee)
  (func $mixed-sum
    (param $a (ref $base)) (param $b (ref $sub)) (param $c (ref $arr))
    (param $d (ref i31)) (param $e (ref $ft)) (param $x (ref extern)) (param $guard i32)
    (result i32)
    (drop (local.get $guard))
    (i32.add
      (i32.add
        (i32.add
          (i32.add
            (i32.add
              (struct.get $base 0 (local.get $a))
              (struct.get $sub 1 (local.get $b)))
            (array.get $arr (local.get $c) (i32.const 1)))
          (i31.get_u (local.get $d)))
        (call_ref $ft (local.get $e)))
      (struct.get $base 0
        (ref.cast (ref $base) (any.convert_extern (local.get $x))))))
  (func $probe (result i32)
    (call $mixed-sum
      (struct.new $base (i32.const {vals[0]}))
      (struct.new $sub (i32.const 7) (i32.const {vals[1]}))
      (array.new_fixed $arr 2 (i32.const 9) (i32.const {vals[2]}))
      (ref.i31 (i32.const {vals[3]}))
      (ref.func $callee)
      (extern.convert_any (struct.new $base (i32.const {vals[4] + vals[5]})))
      (call $churn (i32.const {_CHURN}))))
{_hot_driver()}
)"""
    return "mixed-heaps", expected, _common(extra)


_FAMILIES = (_locals, _operand, _multivalue, _loop_phi, _mixed)


def stackmap_gen(seed):
    """Return (label, export, expected, wat) for a stack-map/liveness stress case."""
    family = _FAMILIES[seed % len(_FAMILIES)]
    n = _COUNTS[(seed // len(_FAMILIES)) % len(_COUNTS)]
    label, expected, wat = family(seed, n)
    signed = expected if expected < 0x80000000 else expected - 0x100000000
    return f"stackmap-{label}", "f", f"OK {signed}", wat
