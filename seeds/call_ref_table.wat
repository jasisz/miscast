;; typed function reference through a funcref table — gives mutate call_ref / table / func-subtyping surface.
(module
  (type $sup (sub (func (param (ref eq)) (result (ref eq)))))
  (type $sub (sub $sup (func (param (ref any)) (result (ref i31)))))
  (func $impl (type $sub) (param (ref any)) (result (ref i31))
    (ref.i31
      (i32.add
        (i31.get_s (ref.cast (ref i31) (local.get 0)))
        (i32.const 4))))
  (table $t 1 funcref)
  (elem (i32.const 0) func $impl)
  (func (export "f") (result i32)
    (i31.get_s
      (ref.cast (ref i31)
        (call_ref $sup
          (ref.i31 (i32.const 38))
          (ref.cast (ref $sup) (table.get $t (i32.const 0))))))))
