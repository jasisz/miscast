;; reason: ref.test target heap type is in a different hierarchy than the operand
(module (func (export "f") (result i32) (ref.test (ref extern) (ref.i31 (i32.const 5)))))
