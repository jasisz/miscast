;; reason: throw_ref's operand must be an exnref, not a non-reference value (i32 here)
(module
  (func (export "f")
    (i32.const 0)
    (throw_ref)))
