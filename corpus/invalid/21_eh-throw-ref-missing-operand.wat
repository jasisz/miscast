;; reason: throw_ref requires an exnref operand on the stack; here the stack is empty at throw_ref
(module
  (func (export "f") (result i32)
    (throw_ref)
    (i32.const 0)))
