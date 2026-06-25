;; reason: block leaves i64 where i32 is declared
(module (func (export "f") (result i32) (block (result i32) (i64.const 7))))
