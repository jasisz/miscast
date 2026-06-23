(module (rec (type $inner (struct (field i32))) (type $a (struct (field (ref $inner)))))
 (rec (type $inner2 (struct (field i32))) (type $b (struct (field (ref $inner2)))))
 (func (export "f") (result i32) (ref.test (ref $a) (struct.new $b (struct.new $inner2 (i32.const 5))))))
