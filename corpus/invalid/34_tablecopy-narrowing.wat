;; reason: table.copy where the source table element type is not a subtype of the destination table element type
(module
  (type $s (sub (struct (field i32))))
  (table $dst 1 (ref null $s))
  (table $src 1 (ref null eq))
  (func (export "f") (result i32)
    (table.copy $dst $src (i32.const 0) (i32.const 0) (i32.const 1))
    (i32.const 1)))
