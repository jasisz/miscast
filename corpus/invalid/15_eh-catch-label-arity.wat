;; reason: a try_table catch label must have the same number of types as the tag (tag has 2 params, label has 1)
(module
  (tag $e (param i32 i32))
  (func (export "f") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 1)
        (return))
      (unreachable))))
