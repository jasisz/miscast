;; reason: subtype extends a final type
(module (rec (type $base (sub final (struct (field i32)))) (type $sub (sub $base (struct (field i32))))) (func (export "f") (result i32) (i32.const 1)))
