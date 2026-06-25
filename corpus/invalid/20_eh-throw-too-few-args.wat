;; reason: a throw must supply every tag param; the tag wants (i32 i32) but only one operand is on the stack
(module
  (tag $e (param i32 i32))
  (func (export "f")
    (i32.const 1)
    (throw $e)))
