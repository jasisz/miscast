;; ref.test to a CONCRETE function type (issue #96).
;; $g has type $ft; testing it against (ref $ft) should succeed.
;; Mutate re-points the test at the declared type ($ft).
;;   $ft -> engine = 1 (g is a $ft);  Talos = 0 (concrete funcref test always fails)
(module
  (type $ft (func (result i32)))
  (func $g (type $ft) (i32.const 5))
  (elem declare func $g)
  (func (export "f") (result i32)
    (ref.test (ref $ft) (ref.func $g))))
