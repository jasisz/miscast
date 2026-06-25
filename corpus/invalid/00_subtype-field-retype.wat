;; reason: subtype retypes a supertype field (i32 -> i64)
(module (rec (type $base (sub (struct (field (mut i32))))) (type $sub (sub $base (struct (field (mut i64)))))) (func (export "f") (result i32) (i32.const 1)))
