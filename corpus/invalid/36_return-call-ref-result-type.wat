;; reason: return_call_ref type slot expects an i32-returning function but receives an i64-returning function reference
(module
  (type $want (sub (func (result i32))))
  (type $got (sub (func (result i64))))
  (func $g (type $got) (result i64)
    (i64.const 1))
  (elem declare func $g)
  (func (export "f") (result i32)
    (return_call_ref $want (ref.func $g))))
