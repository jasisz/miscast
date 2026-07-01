"""Generated stateful .wast scripts.

All earlier generated modes run one exported function on a fresh instance. These scripts exercise the part
that one-shot runners cannot model: several invokes in order, persistent globals/memories/tables, passive
segment state after drop, multiple registered module instances sharing imports, start-function side effects,
assert_unlinkable checks, and repeated `register` rebinding/capture semantics.
"""
import os

from .config import WORK


class _Rng:
    def __init__(self, seed):
        self.state = (0xD00D_F00D ^ (seed * 0x9E37_79B9)) & 0xFFFFFFFF

    def pick(self, n):
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        return self.state % n


def _wast_bytes(vals):
    return "".join(f"\\{v & 0xFF:02x}" for v in vals)


def _script_single_state(seed):
    a, b, c = 5 + seed, 17 + seed * 3, 29 + seed * 5
    s1 = a
    s2 = a + b
    s3 = s2 + c
    expected = s3 * 3 + (s2 & 0xFF) * 5 + (s3 & 0xFF) * 7
    script = f"""(module
  (global $g (mut i32) (i32.const 0))
  (memory 1)
  (func $f0 (result i32) (i32.const 100))
  (func $f1 (result i32) (i32.const 200))
  (table 2 funcref)
  (elem (i32.const 0) func $f0 $f1)
  (type $f (func (result i32)))
  (func (export "step") (param i32) (result i32)
    (global.set $g (i32.add (global.get $g) (local.get 0)))
    (i32.store8 (global.get $g) (global.get $g))
    (global.get $g))
  (func (export "via-table") (param i32) (result i32)
    (call_indirect (type $f) (local.get 0)))
  (func (export "check") (result i32)
    (i32.add
      (i32.add
        (i32.mul (global.get $g) (i32.const 3))
        (i32.mul (i32.load8_u (i32.const {s2})) (i32.const 5)))
      (i32.mul (i32.load8_u (i32.const {s3})) (i32.const 7)))))
(assert_return (invoke "step" (i32.const {a})) (i32.const {s1}))
(assert_return (invoke "via-table" (i32.const 0)) (i32.const 100))
(assert_return (invoke "step" (i32.const {b})) (i32.const {s2}))
(assert_return (invoke "via-table" (i32.const 1)) (i32.const 200))
(assert_return (invoke "step" (i32.const {c})) (i32.const {s3}))
(assert_return (invoke "check") (i32.const {expected}))"""
    return "single-instance-memory-global-table", 6, script


def _script_passive_segments(seed):
    vals = [(seed * 11 + i * 17 + 41) & 0xFF for i in range(8)]
    f0, f1 = 300 + seed, 400 + seed
    expected = vals[1] * 3 + vals[4] * 5 + f0 * 7 + f1 * 11
    script = f"""(module
  (type $f (func (result i32)))
  (memory 1)
  (table 2 funcref)
  (data $d "{_wast_bytes(vals)}")
  (elem $e func $f0 $f1)
  (func $f0 (result i32) (i32.const {f0}))
  (func $f1 (result i32) (i32.const {f1}))
  (func (export "init-data")
    (memory.init $d (i32.const 10) (i32.const 1) (i32.const 5)))
  (func (export "init-elem")
    (table.init $e (i32.const 0) (i32.const 0) (i32.const 2)))
  (func (export "drop-data") (data.drop $d))
  (func (export "drop-elem") (elem.drop $e))
  (func (export "call") (param i32) (result i32)
    (call_indirect (type $f) (local.get 0)))
  (func (export "check") (result i32)
    (i32.add
      (i32.add
        (i32.mul (i32.load8_u (i32.const 10)) (i32.const 3))
        (i32.mul (i32.load8_u (i32.const 13)) (i32.const 5)))
      (i32.add
        (i32.mul (call_indirect (type $f) (i32.const 0)) (i32.const 7))
        (i32.mul (call_indirect (type $f) (i32.const 1)) (i32.const 11))))))
(invoke "init-data")
(invoke "init-elem")
(assert_return (invoke "check") (i32.const {expected}))
(invoke "drop-data")
(invoke "drop-elem")
(assert_trap (invoke "init-data") "out of bounds")
(assert_trap (invoke "init-elem") "out of bounds")"""
    return "passive-data-elem-drop-state", 7, script


def _script_registered_memory(seed):
    v1, v2 = 70 + seed, 90 + seed * 2
    expected = v1 * 3 + v2 * 5
    script = f"""(module $A
  (memory (export "mem") 1)
  (func (export "put-a") (param i32)
    (i32.store8 (i32.const 0) (local.get 0)))
  (func (export "read-a") (result i32)
    (i32.load8_u (i32.const 1))))
(register "shared" $A)
(module $B
  (import "shared" "mem" (memory 1))
  (func (export "put-b") (param i32)
    (i32.store8 (i32.const 1) (local.get 0)))
  (func (export "sum") (result i32)
    (i32.add
      (i32.mul (i32.load8_u (i32.const 0)) (i32.const 3))
      (i32.mul (i32.load8_u (i32.const 1)) (i32.const 5)))))
(invoke $A "put-a" (i32.const {v1}))
(invoke $B "put-b" (i32.const {v2}))
(assert_return (invoke $A "read-a") (i32.const {v2}))
(assert_return (invoke $B "sum") (i32.const {expected}))"""
    return "registered-shared-memory", 4, script


def _script_registered_table(seed):
    a, b, c = 11 + seed, 22 + seed, 33 + seed
    expected = a * 3 + b * 5 + c * 7
    script = f"""(module $Host
  (type $f (func (result i32)))
  (table (export "tab") 3 funcref)
  (func $ha (result i32) (i32.const {a}))
  (func $hb (result i32) (i32.const {b}))
  (elem $eh func $ha $hb)
  (func (export "init-host")
    (table.init $eh (i32.const 0) (i32.const 0) (i32.const 2))))
(register "tabhost" $Host)
(module $Client
  (type $f (func (result i32)))
  (import "tabhost" "tab" (table 3 funcref))
  (func $hc (result i32) (i32.const {c}))
  (elem $ec func $hc)
  (func (export "init-client")
    (table.init $ec (i32.const 2) (i32.const 0) (i32.const 1)))
  (func (export "check") (result i32)
    (i32.add
      (i32.add
        (i32.mul (call_indirect (type $f) (i32.const 0)) (i32.const 3))
        (i32.mul (call_indirect (type $f) (i32.const 1)) (i32.const 5)))
      (i32.mul (call_indirect (type $f) (i32.const 2)) (i32.const 7)))))
(invoke $Host "init-host")
(invoke $Client "init-client")
(assert_return (invoke $Client "check") (i32.const {expected}))"""
    return "registered-shared-table", 3, script


def _script_multimemory(seed):
    a, b = 20 + seed * 3, 150 - seed
    expected = a * 3 + b * 5 + 2 * 7
    script = f"""(module
  (memory $m0 1)
  (memory $m1 1)
  (func (export "seed")
    (i32.store8 $m0 (i32.const 0) (i32.const {a}))
    (i32.store8 $m1 (i32.const 0) (i32.const {b})))
  (func (export "copy-grow")
    (memory.copy $m1 $m0 (i32.const 65535) (i32.const 0) (i32.const 1))
    (drop (memory.grow $m1 (i32.const 1))))
  (func (export "check") (result i32)
    (i32.add
      (i32.add
        (i32.mul (i32.load8_u $m0 (i32.const 0)) (i32.const 3))
        (i32.mul (i32.load8_u $m1 (i32.const 0)) (i32.const 5)))
      (i32.mul (memory.size $m1) (i32.const 7)))))
(invoke "seed")
(assert_return (invoke "check") (i32.const {a * 3 + b * 5 + 7}))
(invoke "copy-grow")
(assert_return (invoke "check") (i32.const {expected}))"""
    return "multi-memory-persistent-grow-copy", 4, script


def _script_gc_global(seed):
    a, b, c = 100 + seed, 7 + seed, 9 + seed
    expected = (a + b + c) * 3 + (a + b) * 5
    script = f"""(module
  (type $cell (sub (struct (field (mut i32)))))
  (global $g (mut (ref null $cell)) (ref.null $cell))
  (func (export "init") (param i32)
    (global.set $g (struct.new $cell (local.get 0))))
  (func (export "add") (param i32) (result i32)
    (struct.set $cell 0 (ref.as_non_null (global.get $g))
      (i32.add
        (struct.get $cell 0 (ref.as_non_null (global.get $g)))
        (local.get 0)))
    (struct.get $cell 0 (ref.as_non_null (global.get $g))))
  (func (export "check") (result i32)
    (struct.get $cell 0 (ref.as_non_null (global.get $g)))))
(invoke "init" (i32.const {a}))
(assert_return (invoke "add" (i32.const {b})) (i32.const {a + b}))
(assert_return (invoke "check") (i32.const {a + b}))
(assert_return (invoke "add" (i32.const {c})) (i32.const {a + b + c}))
(assert_return (invoke "check") (i32.const {a + b + c}))
(assert_return (invoke "add" (i32.const 0)) (i32.const {a + b + c}))"""
    # The repeated read/add assertions force the same GC object in the mutable global to survive across invokes.
    return "gc-global-reference-state", 6, script + f"\n;; checksum {expected}"


def _script_cross_module_gc_global(seed):
    base, bump = 300 + seed * 5, 40 + seed
    expected = base + bump
    script = f"""(module $A
  (type $cell (sub (struct (field (mut i32)))))
  (global (export "g") (mut (ref null $cell)) (ref.null $cell))
  (func (export "init") (param i32)
    (global.set 0 (struct.new $cell (local.get 0))))
  (func (export "read") (result i32)
    (struct.get $cell 0 (ref.as_non_null (global.get 0)))))
(register "gcglobal" $A)
(module $B
  (type $cell (sub (struct (field (mut i32)))))
  (import "gcglobal" "g" (global $g (mut (ref null $cell))))
  (func (export "bump") (param i32) (result i32)
    (struct.set $cell 0 (ref.as_non_null (global.get $g))
      (i32.add
        (struct.get $cell 0 (ref.as_non_null (global.get $g)))
        (local.get 0)))
    (struct.get $cell 0 (ref.as_non_null (global.get $g)))))
(invoke $A "init" (i32.const {base}))
(assert_return (invoke $A "read") (i32.const {base}))
(assert_return (invoke $B "bump" (i32.const {bump})) (i32.const {expected}))
(assert_return (invoke $A "read") (i32.const {expected}))"""
    return "cross-module-gc-global-structural-import", 4, script


def _script_cross_module_gc_table(seed):
    old, new = 410 + seed, 1410 + seed
    script = f"""(module $A
  (type $cell (sub (struct (field (mut i32)))))
  (table $t (export "tab") 2 (ref null $cell))
  (func (export "seed") (param i32)
    (table.set $t (i32.const 0) (struct.new $cell (local.get 0))))
  (func (export "read") (param i32) (result i32)
    (struct.get $cell 0 (ref.as_non_null (table.get $t (local.get 0))))))
(register "gctable" $A)
(module $B
  (type $cell (sub (struct (field (mut i32)))))
  (import "gctable" "tab" (table $t 2 (ref null $cell)))
  (func (export "copy-bump") (param i32) (result i32)
    (table.copy $t $t (i32.const 1) (i32.const 0) (i32.const 1))
    (struct.set $cell 0 (ref.as_non_null (table.get $t (i32.const 1))) (local.get 0))
    (struct.get $cell 0 (ref.as_non_null (table.get $t (i32.const 0)))))
  (func (export "read-b") (param i32) (result i32)
    (struct.get $cell 0 (ref.as_non_null (table.get $t (local.get 0))))))
(invoke $A "seed" (i32.const {old}))
(assert_return (invoke $A "read" (i32.const 0)) (i32.const {old}))
(assert_return (invoke $B "copy-bump" (i32.const {new})) (i32.const {new}))
(assert_return (invoke $A "read" (i32.const 1)) (i32.const {new}))
(assert_return (invoke $B "read-b" (i32.const 0)) (i32.const {new}))"""
    return "cross-module-gc-table-structural-import", 5, script


def _script_cross_module_gc_func(seed):
    a, b = 520 + seed * 3, 37 + seed
    expected = a + b
    script = f"""(module $A
  (type $cell (sub (struct (field (mut i32)))))
  (func (export "make") (param i32) (result (ref $cell))
    (struct.new $cell (local.get 0))))
(register "gcfun" $A)
(module $B
  (type $cell (sub (struct (field (mut i32)))))
  (type $make (func (param i32) (result (ref $cell))))
  (import "gcfun" "make" (func $make (type $make)))
  (global $g (mut (ref null $cell)) (ref.null $cell))
  (func (export "store") (param i32) (result i32)
    (global.set $g (call $make (local.get 0)))
    (struct.get $cell 0 (ref.as_non_null (global.get $g))))
  (func (export "bump") (param i32) (result i32)
    (struct.set $cell 0 (ref.as_non_null (global.get $g))
      (i32.add
        (struct.get $cell 0 (ref.as_non_null (global.get $g)))
        (local.get 0)))
    (struct.get $cell 0 (ref.as_non_null (global.get $g)))))
(assert_return (invoke $B "store" (i32.const {a})) (i32.const {a}))
(assert_return (invoke $B "bump" (i32.const {b})) (i32.const {expected}))
(assert_return (invoke $B "bump" (i32.const 0)) (i32.const {expected}))"""
    return "cross-module-gc-function-result-import", 3, script


def _script_cross_module_typed_funcref_table(seed):
    x, a, b = 9 + seed, 600 + seed * 2, 900 + seed * 3
    script = f"""(module $A
  (type $ft (sub (func (param i32) (result i32))))
  (table $tab (export "tab") 2 (ref null $ft))
  (func $fa (type $ft) (param i32) (result i32)
    (i32.add (local.get 0) (i32.const {a})))
  (func $fb (type $ft) (param i32) (result i32)
    (i32.add (i32.mul (local.get 0) (i32.const 3)) (i32.const {b})))
  (elem declare func $fa $fb)
  (func (export "seed")
    (table.set $tab (i32.const 0) (ref.func $fa))
    (table.set $tab (i32.const 1) (ref.func $fb))))
(register "functable" $A)
(module $B
  (type $ft (sub (func (param i32) (result i32))))
  (import "functable" "tab" (table $tab 2 (ref null $ft)))
  (func (export "call") (param i32 i32) (result i32)
    (call_ref $ft
      (local.get 1)
      (ref.as_non_null (table.get $tab (local.get 0))))))
(invoke $A "seed")
(assert_return (invoke $B "call" (i32.const 0) (i32.const {x})) (i32.const {x + a}))
(assert_return (invoke $B "call" (i32.const 1) (i32.const {x})) (i32.const {x * 3 + b}))"""
    return "cross-module-typed-funcref-table-import", 3, script


def _script_start_import_side_effects(seed):
    a, b = 13 + seed, 27 + seed * 2
    x = 5 + seed
    script = f"""(module $Host
  (type $ft (sub (func (param i32) (result i32))))
  (global $g (export "g") (mut i32) (i32.const 0))
  (memory $m (export "m") 1)
  (table $tab (export "tab") 2 (ref null $ft))
  (func (export "read-g") (result i32) (global.get $g))
  (func (export "read-m") (param i32) (result i32)
    (i32.load8_u (local.get 0)))
  (func (export "call") (param i32 i32) (result i32)
    (call_ref $ft
      (local.get 1)
      (ref.as_non_null (table.get $tab (local.get 0))))))
(register "starthost" $Host)
(module $B
  (type $ft (sub (func (param i32) (result i32))))
  (import "starthost" "g" (global $g (mut i32)))
  (import "starthost" "m" (memory $m 1))
  (import "starthost" "tab" (table $tab 2 (ref null $ft)))
  (func $fb (type $ft) (param i32) (result i32)
    (i32.add (local.get 0) (i32.const {100 + seed})))
  (elem declare func $fb)
  (func $start
    (global.set $g (i32.add (global.get $g) (i32.const {a})))
    (i32.store8 (i32.const 10) (i32.const {a}))
    (table.set $tab (i32.const 0) (ref.func $fb)))
  (start $start))
(assert_return (invoke $Host "read-g") (i32.const {a}))
(assert_return (invoke $Host "read-m" (i32.const 10)) (i32.const {a}))
(assert_return (invoke $Host "call" (i32.const 0) (i32.const {x})) (i32.const {x + 100 + seed}))
(module $C
  (type $ft (sub (func (param i32) (result i32))))
  (import "starthost" "g" (global $g (mut i32)))
  (import "starthost" "m" (memory $m 1))
  (import "starthost" "tab" (table $tab 2 (ref null $ft)))
  (func $fc (type $ft) (param i32) (result i32)
    (i32.add (i32.mul (local.get 0) (i32.const 2)) (i32.const {200 + seed})))
  (elem declare func $fc)
  (func $start
    (global.set $g (i32.add (global.get $g) (i32.const {b})))
    (i32.store8 (i32.const 11) (i32.const {b}))
    (table.set $tab (i32.const 1) (ref.func $fc)))
  (start $start))
(assert_return (invoke $Host "read-g") (i32.const {a + b}))
(assert_return (invoke $Host "read-m" (i32.const 11)) (i32.const {b}))
(assert_return (invoke $Host "call" (i32.const 1) (i32.const {x})) (i32.const {x * 2 + 200 + seed}))"""
    return "start-import-side-effects-typed-table", 6, script


def _script_start_gc_object_to_host(seed):
    val = 700 + seed * 7
    bump = 33 + seed
    script = f"""(module $Host
  (type $cell (sub (struct (field (mut i32)))))
  (global $g (export "g") (mut (ref null $cell)) (ref.null $cell))
  (table $tab (export "tab") 1 (ref null $cell))
  (func (export "read-g") (result i32)
    (struct.get $cell 0 (ref.as_non_null (global.get $g))))
  (func (export "read-t") (result i32)
    (struct.get $cell 0 (ref.as_non_null (table.get $tab (i32.const 0)))))
  (func (export "same") (result i32)
    (ref.eq (global.get $g) (table.get $tab (i32.const 0))))
  (func (export "bump-host") (param i32) (result i32)
    (struct.set $cell 0 (ref.as_non_null (global.get $g))
      (i32.add
        (struct.get $cell 0 (ref.as_non_null (global.get $g)))
        (local.get 0)))
    (struct.get $cell 0 (ref.as_non_null (table.get $tab (i32.const 0))))))
(register "gchost" $Host)
(module $Maker
  (type $cell (sub (struct (field (mut i32)))))
  (import "gchost" "g" (global $g (mut (ref null $cell))))
  (import "gchost" "tab" (table $tab 1 (ref null $cell)))
  (func $start (local $x (ref $cell))
    (local.set $x (struct.new $cell (i32.const {val})))
    (global.set $g (local.get $x))
    (table.set $tab (i32.const 0) (local.get $x)))
  (start $start))
(assert_return (invoke $Host "read-g") (i32.const {val}))
(assert_return (invoke $Host "read-t") (i32.const {val}))
(assert_return (invoke $Host "same") (i32.const 1))
(assert_return (invoke $Host "bump-host" (i32.const {bump})) (i32.const {val + bump}))
(assert_return (invoke $Host "read-t") (i32.const {val + bump}))
(assert_return (invoke $Host "same") (i32.const 1))"""
    return "start-gc-object-to-host-alias", 6, script


def _script_dropped_segment_zero_len(seed):
    mem_val, call_val = 80 + (seed & 31), 900 + seed
    expected = mem_val + call_val
    script = f"""(module
  (type $f (func (result i32)))
  (memory 1)
  (table 1 funcref)
  (data $d "abc")
  (elem $e func $unused)
  (func $unused (result i32) (i32.const {call_val + 1}))
  (func $sentinel (result i32) (i32.const {call_val}))
  (elem (i32.const 0) func $sentinel)
  (func (export "seed")
    (i32.store8 (i32.const 65535) (i32.const {mem_val})))
  (func (export "drop-segments")
    (data.drop $d)
    (elem.drop $e))
  (func (export "zero-data-at-end")
    (memory.init $d (i32.const 65536) (i32.const 0) (i32.const 0)))
  (func (export "zero-elem-at-end")
    (table.init $e (i32.const 1) (i32.const 0) (i32.const 0)))
  (func (export "nonzero-data-after-drop")
    (memory.init $d (i32.const 0) (i32.const 0) (i32.const 1)))
  (func (export "nonzero-elem-after-drop")
    (table.init $e (i32.const 0) (i32.const 0) (i32.const 1)))
  (func (export "check") (result i32)
    (i32.add
      (i32.load8_u (i32.const 65535))
      (call_indirect (type $f) (i32.const 0)))))
(invoke "seed")
(invoke "drop-segments")
(invoke "zero-data-at-end")
(invoke "zero-elem-at-end")
(assert_return (invoke "check") (i32.const {expected}))
(assert_trap (invoke "nonzero-data-after-drop") "out of bounds")
(assert_trap (invoke "nonzero-elem-after-drop") "out of bounds")"""
    return "dropped-segment-zero-length-init", 7, script


def _script_unlink_table_type(seed):
    script = """(module $A
  (table (export "tab") 1 (ref null eq)))
(register "badtab" $A)
(assert_unlinkable
  (module
    (type $cell (sub (struct (field i32))))
    (import "badtab" "tab" (table 1 (ref null $cell))))
  "incompatible import type")"""
    return "unlink-table-element-narrowing", 3, script


def _script_unlink_gc_global_type(seed):
    script = """(module $A
  (type $a (sub (struct (field i32))))
  (global (export "g") (mut (ref null $a)) (ref.null $a)))
(register "badglobal" $A)
(assert_unlinkable
  (module
    (type $b (sub (struct (field i32) (field i32))))
    (import "badglobal" "g" (global (mut (ref null $b)))))
  "incompatible import type")"""
    return "unlink-gc-global-structural-mismatch", 3, script


def _script_unlink_gc_func_result(seed):
    script = """(module $A
  (type $a (sub (struct (field i32))))
  (func (export "make") (result (ref $a))
    (struct.new $a (i32.const 1))))
(register "badfun" $A)
(assert_unlinkable
  (module
    (type $b (sub (struct (field i32) (field i32))))
    (type $want (func (result (ref $b))))
    (import "badfun" "make" (func $make (type $want))))
  "incompatible import type")"""
    return "unlink-gc-function-result-mismatch", 3, script


def _script_register_rebind_func_capture(seed):
    a, c = 1000 + seed, 2000 + seed * 3
    script = f"""(module $A
  (func (export "f") (result i32) (i32.const {a})))
(register "slot" $A)
(module $B
  (import "slot" "f" (func $f (result i32)))
  (func (export "call") (result i32)
    (call $f)))
(assert_return (invoke $B "call") (i32.const {a}))
(module $C
  (func (export "f") (result i32) (i32.const {c})))
(register "slot" $C)
(module $D
  (import "slot" "f" (func $f (result i32)))
  (func (export "call") (result i32)
    (call $f)))
(assert_return (invoke $B "call") (i32.const {a}))
(assert_return (invoke $D "call") (i32.const {c}))"""
    return "register-rebind-function-capture", 7, script


def _script_register_rebind_memory_capture(seed):
    b, d = 31 + seed, 73 + seed * 2
    script = f"""(module $A
  (memory (export "m") 1)
  (func (export "read") (result i32)
    (i32.load8_u (i32.const 0))))
(register "memslot" $A)
(module $B
  (import "memslot" "m" (memory 1))
  (func (export "put") (param i32)
    (i32.store8 (i32.const 0) (local.get 0)))
  (func (export "read") (result i32)
    (i32.load8_u (i32.const 0))))
(module $C
  (memory (export "m") 1)
  (func (export "read") (result i32)
    (i32.load8_u (i32.const 0))))
(register "memslot" $C)
(module $D
  (import "memslot" "m" (memory 1))
  (func (export "put") (param i32)
    (i32.store8 (i32.const 0) (local.get 0)))
  (func (export "read") (result i32)
    (i32.load8_u (i32.const 0))))
(invoke $B "put" (i32.const {b}))
(invoke $D "put" (i32.const {d}))
(assert_return (invoke $B "read") (i32.const {b}))
(assert_return (invoke $D "read") (i32.const {d}))
(assert_return (invoke $A "read") (i32.const {b}))
(assert_return (invoke $C "read") (i32.const {d}))"""
    return "register-rebind-memory-capture", 10, script


_FAMILIES = [_script_single_state, _script_passive_segments, _script_registered_memory,
             _script_registered_table, _script_multimemory, _script_gc_global,
             _script_cross_module_gc_global, _script_cross_module_gc_table,
             _script_cross_module_gc_func, _script_cross_module_typed_funcref_table,
             _script_start_import_side_effects, _script_start_gc_object_to_host,
             _script_dropped_segment_zero_len, _script_unlink_table_type,
             _script_unlink_gc_global_type, _script_unlink_gc_func_result,
             _script_register_rebind_func_capture, _script_register_rebind_memory_capture]

WASMTIME_WAST_FLAGS = "function-references=y,gc=y,exceptions=y,tail-call=y,multi-memory=y,simd=y,bulk-memory=y"


def seqscript_gen(seed):
    """Return (label, action_count, wast_script): the seed-th generated stateful .wast script."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, actions, script = fam(seed // len(_FAMILIES))
    return f"seqscript-{label}", actions, script + "\n"


def write_seqscript_groups(n):
    """Write generated .wast scripts under work/seqscript and return conformance-style groups."""
    outdir = os.path.join(WORK, "seqscript")
    os.makedirs(outdir, exist_ok=True)
    groups = []
    for i in range(n):
        label, actions, script = seqscript_gen(i)
        path = os.path.join(outdir, f"{i:04d}-{label}.wast")
        with open(path, "w") as f:
            f.write(script)
        groups.append({"name": f"seqscript{i}|{label}", "src": os.path.abspath(path),
                       "nstateful": actions, "generated": True})
    return groups


if __name__ == "__main__":
    import shutil
    import subprocess

    print("=== seqscript: generated stateful .wast scripts ===")
    bad = 0
    groups = write_seqscript_groups(2 * len(_FAMILIES))
    if not shutil.which("wasmtime"):
        for g in groups:
            print(f"  OK  {g['name']} wrote {g['src']}")
    else:
        for g in groups:
            p = subprocess.run(["wasmtime", "wast", "-W", WASMTIME_WAST_FLAGS, g["src"]],
                               capture_output=True, text=True)
            ok = p.returncode == 0
            bad += not ok
            tail = (p.stderr or p.stdout).strip().splitlines()[-1][:120] if not ok and (p.stderr or p.stdout).strip() else ""
            print(f"  {'OK ' if ok else 'FAIL'} {g['name']:48} {tail}")
    print(f"\n{len(groups)} scripts, {bad} failing wasmtime wast")
