;; reason: table.fill value type must be a subtype of the table element type
(module
  (type $s (sub (struct (field i32))))
  (table $t 1 (ref null $s))
  (func (export "f") (result i32)
    (table.fill $t (i32.const 0) (ref.i31 (i32.const 7)) (i32.const 1))
    (i32.const 1)))
