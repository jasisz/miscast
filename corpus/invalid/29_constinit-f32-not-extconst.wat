;; reason: const-init: f32.add is not an extended-const operator (only i32/i64 add/sub/mul are constant)
(module
  (global $g f32 (f32.add (f32.const 1) (f32.const 2)))
  (func (export "f") (result f32) (global.get $g)))
