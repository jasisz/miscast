;; reason: function body produces i64 where i32 is declared
(module (func (export "f") (result i32) (i64.const 99)))
