;; reason: subtype retypes an immutable field (i32 -> f32)
(module (rec (type $base (sub (struct (field i32)))) (type $sub (sub $base (struct (field f32))))) (func (export "f") (result i32) (i32.const 1)))
