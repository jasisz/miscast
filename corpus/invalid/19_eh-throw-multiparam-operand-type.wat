;; reason: each throw operand must match its tag param type; the tag wants (i32 i32) but the top operand is f32
(module
  (tag $e (param i32 i32))
  (func (export "f")
    (i32.const 1)
    (f32.const 2)
    (throw $e)))
