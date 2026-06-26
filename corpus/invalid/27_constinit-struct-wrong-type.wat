;; reason: const-init: struct.new of $s assigned to a global typed (ref $other) — unrelated struct types
(module
  (type $s (sub (struct (field i32))))
  (type $other (sub (struct (field f64) (field f64))))
  (global $g (ref $other) (struct.new $s (i32.const 5)))
  (func (export "f") (result i32) (i32.const 0)))
