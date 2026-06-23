;; call_indirect with GC-reference result covariance.
;; (func (result eqref)) <: (func (result anyref))  since  eqref <: anyref.
;; impl returns anyref. Mutate re-points the call at $superG/$subG.
;;   $superG -> control (impl matches)             -> both run, ref.is_null = 1
;;   $subG   -> needs a func returning <= eqref;
;;              impl returns anyref (NOT <: eqref)  -> engine TRAPs (ill-typed)
(module
  (rec
    (type $superG (sub (func (result anyref))))
    (type $subG   (sub $superG (func (result eqref)))))
  (func $implG (type $superG) (ref.null any))
  (table 1 funcref)
  (elem (i32.const 0) $implG)
  (func (export "f") (result i32)
    (i32.const 0) (call_indirect (type $superG)) (ref.is_null)))
