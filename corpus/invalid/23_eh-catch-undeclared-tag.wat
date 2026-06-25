;; reason: a try_table catch tag index must be in bounds; this catch names tag index 7 but only tag 0 exists
(module
  (tag $e (param i32))
  (func (export "f") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch 7 $h)
        (i32.const 1)
        (throw $e)
        (unreachable))
      (return))
    (i32.add (i32.const 1))))
