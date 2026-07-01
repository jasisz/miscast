;; reason: array.fill value type must be a subtype of the array element type
(module
  (type $s (sub (struct (field i32))))
  (type $arr (array (mut (ref null $s))))
  (func (export "f") (result i32)
    (local $a (ref $arr))
    (local.set $a (array.new_default $arr (i32.const 1)))
    (array.fill $arr (local.get $a) (i32.const 0) (ref.i31 (i32.const 9)) (i32.const 1))
    (i32.const 1)))
