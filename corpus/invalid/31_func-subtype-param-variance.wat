;; reason: function subtype narrows a parameter type; parameters are contravariant
(module
  (type $super (sub (func (param (ref eq)) (result (ref eq)))))
  (type $bad (sub $super (func (param (ref i31)) (result (ref eq)))))
  (func (export "f") (result i32) (i32.const 0)))
