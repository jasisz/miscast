;; reason: a try_table must leave its declared result on the stack; this one declares (result i32) but its body is a nop
(module
  (func (export "f") (result i32)
    (block $h (result i32)
      (try_table (result i32)
        (nop))
      (unreachable))))
