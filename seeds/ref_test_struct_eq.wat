;; ref.test against two SEPARATELY-declared, structurally identical structs.
;; The spec compares defined types up to structural (iso-recursive) equivalence,
;; so $a and $b are one type. Mutate re-points the test at $a/$b.
;;   $a -> control (same type)        -> both = 1
;;   $b -> structurally equal to $a   -> engine = 1, Talos = 0 (nominal compare)
(module
  (type $a (struct (field i32)))
  (type $b (struct (field i32)))
  (func (export "f") (result i32)
    (ref.test (ref $a) (struct.new $a (i32.const 7)))))
