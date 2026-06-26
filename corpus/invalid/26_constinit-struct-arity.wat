;; reason: const-init: struct.new in a global initialiser supplies too few fields for the struct type
(module
  (type $s (sub (struct (field i32) (field i32))))
  (global $g (ref $s) (struct.new $s (i32.const 5)))
  (func (export "f") (result i32) (i32.const 0)))
