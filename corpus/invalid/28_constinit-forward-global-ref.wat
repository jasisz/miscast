;; reason: const-init: a global initialiser reads a global defined LATER (forward reference is non-constant)
(module
  (global $a i32 (global.get $b))
  (global $b i32 (i32.const 3))
  (func (export "f") (result i32) (global.get $a)))
