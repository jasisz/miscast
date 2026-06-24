;; two distinct struct types + a non-null ref param — probes ref-swap + nullability.
(module
  (type $s (sub (struct (field i32))))
  (type $t (sub (struct (field i64))))
  (func $g (param (ref $s)) (result i32) (struct.get $s 0 (local.get 0)))
  (func (export "f") (result i32) (call $g (struct.new $s (i32.const 33)))))
