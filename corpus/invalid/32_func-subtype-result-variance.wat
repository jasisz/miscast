;; reason: function subtype widens a result type; results are covariant
(module
  (type $super (sub (func (result (ref i31)))))
  (type $bad (sub $super (func (result (ref eq)))))
  (func (export "f") (result i32) (i32.const 0)))
