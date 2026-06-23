(module (rec (type $a (struct (field (ref null $a))))) (rec (type $b (struct (field (ref null $b)))))
 (func (export "f") (result i32) (ref.test (ref $a) (struct.new_default $b))))
