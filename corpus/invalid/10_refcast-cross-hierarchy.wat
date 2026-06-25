;; reason: ref.cast target heap type is in a different hierarchy than the operand
(module (func (export "f") (result i32) (drop (ref.cast (ref extern) (ref.i31 (i32.const 5)))) (i32.const 7)))
