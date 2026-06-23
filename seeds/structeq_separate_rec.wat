(module (rec (type $a (struct (field i32)))) (rec (type $b (struct (field i32))))
  (func (export "f") (result i32) (ref.test (ref $a) (struct.new $b (i32.const 7)))))
