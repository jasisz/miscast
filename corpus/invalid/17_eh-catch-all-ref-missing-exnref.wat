;; reason: a catch_all_ref label must have exactly one result type (the exnref it forwards); this label has none
(module
  (tag $e)
  (func (export "f") (result i32)
    (block $h
      (try_table (catch_all_ref $h)
        (throw $e))
      (return (i32.const 0)))
    (unreachable)))
