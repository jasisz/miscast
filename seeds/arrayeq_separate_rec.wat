(module (rec (type $a (array (mut i32)))) (rec (type $b (array (mut i32))))
  (func (export "f") (result i32) (ref.test (ref $a) (array.new_default $b (i32.const 1)))))
