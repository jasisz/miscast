"""Deterministic multi-worker races over V8 Shared-Everything GC objects.

Every schedule has the same invariant. Numeric fetch-add returns every old value
exactly once. Reference exchange preserves the multiset of inserted objects. A
reference CAS loop performs a linearizable increment with fresh objects, avoiding
ABA. The d8 harness only distributes one state reference and sums worker results;
the generated Wasm module checks its own final state.
"""

_HEAD = "(module\n  (;; miscast-sharedrace-stress ;;)\n"


def _params(v):
    workers = 2 if v % 2 == 0 else 4
    iterations = (500, 2_000, 8_000, 32_000)[(v // 2) % 4]
    order = "seq_cst" if (v // 4) % 2 == 0 else "acq_rel"
    return workers, iterations, order


def _exports(workers, iterations):
    return f'''  (func (export "workers") (result i32) (i32.const {workers}))
  (func (export "iterations") (result i32) (i32.const {iterations}))
'''


def _counter_struct(v):
    workers, iterations, order = _params(v)
    total = workers * iterations
    old_sum = total * (total - 1) // 2
    wat = _HEAD + f'''  (type $state (shared (struct (field (mut i32)))))
  (func (export "make") (result (ref $state))
    (struct.new $state (i32.const 0)))
{_exports(workers, iterations)}  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (local $i i32) (local $sum i64)
    (loop $again
      (local.set $sum (i64.add (local.get $sum) (i64.extend_i32_u
        (struct.atomic.rmw.add {order} $state 0 (local.get $state) (i32.const 1)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
    (local.get $sum))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or
      (i32.ne (struct.atomic.get {order} $state 0 (local.get $state)) (i32.const {total}))
      (i64.ne (local.get $sum) (i64.const {old_sum})))))'''
    return f"counter-struct-{workers}x{iterations}-{order}", wat


def _counter_array(v):
    workers, iterations, order = _params(v)
    total = workers * iterations
    old_sum = total * (total - 1) // 2
    wat = _HEAD + f'''  (type $state (shared (array (mut i32))))
  (func (export "make") (result (ref $state))
    (array.new $state (i32.const 0) (i32.const 3)))
{_exports(workers, iterations)}  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (local $i i32) (local $sum i64)
    (loop $again
      (local.set $sum (i64.add (local.get $sum) (i64.extend_i32_u
        (array.atomic.rmw.add {order} $state (local.get $state) (i32.const 1) (i32.const 1)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
    (local.get $sum))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or
      (i32.ne (array.atomic.get {order} $state (local.get $state) (i32.const 1)) (i32.const {total}))
      (i64.ne (local.get $sum) (i64.const {old_sum})))))'''
    return f"counter-array-{workers}x{iterations}-{order}", wat


def _ref_xchg_struct(v):
    workers, iterations, order = _params(v)
    total = workers * iterations
    value_sum = total * (total + 1) // 2
    wat = _HEAD + f'''  (type $leaf (shared (struct (field i32))))
  (type $state (shared (struct (field (mut i32)) (field (mut (ref $leaf))))))
  (func (export "make") (result (ref $state))
    (struct.new $state (i32.const 0) (struct.new $leaf (i32.const 0))))
{_exports(workers, iterations)}  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (local $i i32) (local $ticket i32) (local $old (ref $leaf)) (local $sum i64)
    (loop $again
      (local.set $ticket (i32.add (i32.const 1)
        (struct.atomic.rmw.add {order} $state 0 (local.get $state) (i32.const 1))))
      (local.set $old (struct.atomic.rmw.xchg {order} $state 1 (local.get $state)
        (struct.new $leaf (local.get $ticket))))
      (local.set $sum (i64.add (local.get $sum)
        (i64.extend_i32_u (struct.get $leaf 0 (local.get $old)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
    (local.get $sum))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or
      (i32.ne (struct.atomic.get {order} $state 0 (local.get $state)) (i32.const {total}))
      (i64.ne
        (i64.add (local.get $sum) (i64.extend_i32_u (struct.get $leaf 0
          (struct.atomic.get {order} $state 1 (local.get $state)))))
        (i64.const {value_sum})))))'''
    return f"ref-xchg-struct-{workers}x{iterations}-{order}", wat


def _ref_xchg_array(v):
    workers, iterations, order = _params(v)
    total = workers * iterations
    value_sum = total * (total + 1) // 2
    wat = _HEAD + f'''  (type $leaf (shared (struct (field i32))))
  (type $counter (shared (array (mut i32))))
  (type $slot (shared (array (mut (ref $leaf)))))
  (type $state (shared (struct (field (ref $counter)) (field (ref $slot)))))
  (func (export "make") (result (ref $state))
    (struct.new $state
      (array.new $counter (i32.const 0) (i32.const 1))
      (array.new $slot (struct.new $leaf (i32.const 0)) (i32.const 1))))
{_exports(workers, iterations)}  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (local $i i32) (local $ticket i32) (local $old (ref $leaf)) (local $sum i64)
    (loop $again
      (local.set $ticket (i32.add (i32.const 1) (array.atomic.rmw.add {order} $counter
        (struct.get $state 0 (local.get $state)) (i32.const 0) (i32.const 1))))
      (local.set $old (array.atomic.rmw.xchg {order} $slot
        (struct.get $state 1 (local.get $state)) (i32.const 0)
        (struct.new $leaf (local.get $ticket))))
      (local.set $sum (i64.add (local.get $sum)
        (i64.extend_i32_u (struct.get $leaf 0 (local.get $old)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (local.get $n))))
    (local.get $sum))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or
      (i32.ne (array.atomic.get {order} $counter (struct.get $state 0 (local.get $state))
        (i32.const 0)) (i32.const {total}))
      (i64.ne (i64.add (local.get $sum) (i64.extend_i32_u (struct.get $leaf 0
        (array.atomic.get {order} $slot (struct.get $state 1 (local.get $state)) (i32.const 0)))))
        (i64.const {value_sum})))))'''
    return f"ref-xchg-array-{workers}x{iterations}-{order}", wat


def _ref_cas(v, array):
    workers, iterations, order = _params(v)
    total = workers * iterations
    if array:
        state_type = "(type $state (shared (array (mut (ref $leaf)))))"
        make = "(array.new $state (struct.new $leaf (i32.const 0)) (i32.const 1))"
        get = f"(array.atomic.get {order} $state (local.get $state) (i32.const 0))"
        cas = f"(array.atomic.rmw.cmpxchg {order} $state (local.get $state) (i32.const 0)"
        shape = "array"
    else:
        state_type = "(type $state (shared (struct (field (mut (ref $leaf))))))"
        make = "(struct.new $state (struct.new $leaf (i32.const 0)))"
        get = f"(struct.atomic.get {order} $state 0 (local.get $state))"
        cas = f"(struct.atomic.rmw.cmpxchg {order} $state 0 (local.get $state)"
        shape = "struct"
    wat = _HEAD + f'''  (type $leaf (shared (struct (field i32))))
  {state_type}
  (func (export "make") (result (ref $state)) {make})
{_exports(workers, iterations)}  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (local $i i32) (local $old (ref $leaf)) (local $seen (ref $leaf))
    (block $done
      (loop $again
        (local.set $old {get})
        (local.set $seen {cas} (local.get $old)
          (struct.new $leaf (i32.add (struct.get $leaf 0 (local.get $old)) (i32.const 1)))))
        (br_if $again (i32.eqz (ref.eq (local.get $seen) (local.get $old))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $again (i32.lt_u (local.get $i) (local.get $n)))))
    (i64.const 0))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or (i32.ne (struct.get $leaf 0 {get}) (i32.const {total}))
            (i64.ne (local.get $sum) (i64.const 0)))))'''
    return f"ref-cas-{shape}-{workers}x{iterations}-{order}", wat


def _publish_numeric(v, array):
    """Release/acquire publication of a non-atomic i32 payload with a two-party handshake."""
    iterations = (1_000, 4_000, 16_000, 64_000)[v % 4]
    order = "seq_cst" if v < 4 else "acq_rel"
    if array:
        types = '''(type $cell (shared (array (mut i32))))
  (type $state (shared (struct (field (ref $cell)) (field (ref $cell)))))'''
        make = '''(struct.new $state
      (array.new $cell (i32.const 0) (i32.const 1))
      (array.new $cell (i32.const 0) (i32.const 1)))'''
        data_set = "(array.set $cell (struct.get $state 0 (local.get $state)) (i32.const 0) (local.get $i))"
        data_get = "(array.get $cell (struct.get $state 0 (local.get $state)) (i32.const 0))"
        flag_get = f"(array.atomic.get {order} $cell (struct.get $state 1 (local.get $state)) (i32.const 0))"
        flag_set_1 = f"(array.atomic.set {order} $cell (struct.get $state 1 (local.get $state)) (i32.const 0) (i32.const 1))"
        flag_set_0 = f"(array.atomic.set {order} $cell (struct.get $state 1 (local.get $state)) (i32.const 0) (i32.const 0))"
        shape = "array"
    else:
        types = "(type $state (shared (struct (field (mut i32)) (field (mut i32)))))"
        make = "(struct.new $state (i32.const 0) (i32.const 0))"
        data_set = "(struct.set $state 0 (local.get $state) (local.get $i))"
        data_get = "(struct.get $state 0 (local.get $state))"
        flag_get = f"(struct.atomic.get {order} $state 1 (local.get $state))"
        flag_set_1 = f"(struct.atomic.set {order} $state 1 (local.get $state) (i32.const 1))"
        flag_set_0 = f"(struct.atomic.set {order} $state 1 (local.get $state) (i32.const 0))"
        shape = "struct"
    wat = _HEAD + f'''  {types}
  (func (export "make") (result (ref $state)) {make})
{_exports(2, iterations)}  (func $producer (param $state (ref $state)) (param $n i32)
    (local $i i32)
    (local.set $i (i32.const 1))
    (loop $round
      (loop $wait (br_if $wait (i32.ne {flag_get} (i32.const 0))))
      {data_set}
      {flag_set_1}
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $round (i32.le_u (local.get $i) (local.get $n)))))
  (func $consumer (param $state (ref $state)) (param $n i32) (result i64)
    (local $i i32) (local $errors i64)
    (local.set $i (i32.const 1))
    (loop $round
      (loop $wait (br_if $wait (i32.ne {flag_get} (i32.const 1))))
      (local.set $errors (i64.add (local.get $errors)
        (i64.extend_i32_u (i32.ne {data_get} (local.get $i)))))
      {flag_set_0}
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $round (i32.le_u (local.get $i) (local.get $n))))
    (local.get $errors))
  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (if (result i64) (i32.eqz (local.get $id))
      (then (call $producer (local.get $state) (local.get $n)) (i64.const 0))
      (else (call $consumer (local.get $state) (local.get $n)))))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or (i32.ne {flag_get} (i32.const 0))
            (i64.ne (local.get $sum) (i64.const 0)))))'''
    return f"publish-i32-{shape}-2x{iterations}-{order}", wat


def _publish_ref(v, array):
    """Publish a freshly allocated shared reference before a release flag store."""
    iterations = (1_000, 4_000, 16_000, 64_000)[v % 4]
    order = "seq_cst" if v < 4 else "acq_rel"
    if array:
        types = '''(type $leaf (shared (struct (field i32))))
  (type $slot (shared (array (mut (ref $leaf)))))
  (type $flag (shared (array (mut i32))))
  (type $state (shared (struct (field (ref $slot)) (field (ref $flag)))))'''
        make = '''(struct.new $state
      (array.new $slot (struct.new $leaf (i32.const 0)) (i32.const 1))
      (array.new $flag (i32.const 0) (i32.const 1)))'''
        data_set = "(array.set $slot (struct.get $state 0 (local.get $state)) (i32.const 0) (struct.new $leaf (local.get $i)))"
        data_get = "(struct.get $leaf 0 (array.get $slot (struct.get $state 0 (local.get $state)) (i32.const 0)))"
        flag_get = f"(array.atomic.get {order} $flag (struct.get $state 1 (local.get $state)) (i32.const 0))"
        flag_set_1 = f"(array.atomic.set {order} $flag (struct.get $state 1 (local.get $state)) (i32.const 0) (i32.const 1))"
        flag_set_0 = f"(array.atomic.set {order} $flag (struct.get $state 1 (local.get $state)) (i32.const 0) (i32.const 0))"
        shape = "array"
    else:
        types = '''(type $leaf (shared (struct (field i32))))
  (type $state (shared (struct (field (mut (ref $leaf))) (field (mut i32)))))'''
        make = "(struct.new $state (struct.new $leaf (i32.const 0)) (i32.const 0))"
        data_set = "(struct.set $state 0 (local.get $state) (struct.new $leaf (local.get $i)))"
        data_get = "(struct.get $leaf 0 (struct.get $state 0 (local.get $state)))"
        flag_get = f"(struct.atomic.get {order} $state 1 (local.get $state))"
        flag_set_1 = f"(struct.atomic.set {order} $state 1 (local.get $state) (i32.const 1))"
        flag_set_0 = f"(struct.atomic.set {order} $state 1 (local.get $state) (i32.const 0))"
        shape = "struct"
    wat = _HEAD + f'''  {types}
  (func (export "make") (result (ref $state)) {make})
{_exports(2, iterations)}  (func $producer (param $state (ref $state)) (param $n i32)
    (local $i i32)
    (local.set $i (i32.const 1))
    (loop $round
      (loop $wait (br_if $wait (i32.ne {flag_get} (i32.const 0))))
      {data_set}
      {flag_set_1}
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $round (i32.le_u (local.get $i) (local.get $n)))))
  (func $consumer (param $state (ref $state)) (param $n i32) (result i64)
    (local $i i32) (local $errors i64)
    (local.set $i (i32.const 1))
    (loop $round
      (loop $wait (br_if $wait (i32.ne {flag_get} (i32.const 1))))
      (local.set $errors (i64.add (local.get $errors)
        (i64.extend_i32_u (i32.ne {data_get} (local.get $i)))))
      {flag_set_0}
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $round (i32.le_u (local.get $i) (local.get $n))))
    (local.get $errors))
  (func (export "run")
    (param $state (ref $state)) (param $n i32) (param $id i32) (result i64)
    (if (result i64) (i32.eqz (local.get $id))
      (then (call $producer (local.get $state) (local.get $n)) (i64.const 0))
      (else (call $consumer (local.get $state) (local.get $n)))))
  (func (export "check") (param $state (ref $state)) (param $sum i64) (result i32)
    (i32.or (i32.ne {flag_get} (i32.const 0))
            (i64.ne (local.get $sum) (i64.const 0)))))'''
    return f"publish-ref-{shape}-2x{iterations}-{order}", wat


_FAMILIES = (_counter_struct, _counter_array, _ref_xchg_struct, _ref_xchg_array,
             lambda v: _ref_cas(v, False), lambda v: _ref_cas(v, True),
             lambda v: _publish_numeric(v, False), lambda v: _publish_numeric(v, True),
             lambda v: _publish_ref(v, False), lambda v: _publish_ref(v, True))


def sharedrace_gen(seed):
    label, wat = _FAMILIES[seed % len(_FAMILIES)](seed // len(_FAMILIES))
    return f"sharedrace-{label}", "__shared_workers__", "OK 0", wat
