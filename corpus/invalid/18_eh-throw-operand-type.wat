;; reason: a throw's operands must match the tag signature; the tag wants i32 but an i64 is supplied
(module
  (tag $e (param i32))
  (func (export "f")
    (i64.const 1)
    (throw $e)))
