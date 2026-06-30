;; reason: call_ref callee must be a concrete function reference, not an extern reference
(module
  (type $f (sub (func (result i32))))
  (func (export "f") (result i32)
    (call_ref $f (ref.null extern))))
