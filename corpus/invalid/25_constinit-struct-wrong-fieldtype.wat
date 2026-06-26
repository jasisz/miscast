;; reason: const-init: struct.new in a global initialiser supplies an i64 for an i32 field (type mismatch)
(module
  (type $s (sub (struct (field i32))))
  (global $g (ref $s) (struct.new $s (i64.const 5)))
  (func (export "f") (result i32) (i32.const 0)))
