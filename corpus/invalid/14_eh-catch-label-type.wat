;; reason: a try_table catch label's result type must match the tag's params (i32 here), not i64
(module
  (tag $e (param i32))
  (func (export "f") (result i32)
    (block $h (result i64)
      (try_table (result i32) (catch $e $h)
        (i32.const 1)
        (throw $e)
        (unreachable))
      (return))
    (drop)
    (i32.const 0)))
