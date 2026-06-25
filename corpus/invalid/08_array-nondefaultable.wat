;; reason: array.new_default on a non-defaultable element type
(module (type $s (struct (field i32))) (type $arr (array (mut (ref $s)))) (func (export "f") (result i32) (drop (array.new_default $arr (i32.const 3))) (i32.const 1)))
