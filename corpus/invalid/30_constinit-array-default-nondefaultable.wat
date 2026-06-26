;; reason: const-init: array.new_default on an element type (ref $s) that has no default value
(module
  (type $s (sub (struct (field i32))))
  (type $a (sub (array (ref $s))))
  (global $g (ref $a) (array.new_default $a (i32.const 3)))
  (func (export "f") (result i32) (i32.const 0)))
