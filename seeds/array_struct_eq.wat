;; array analog of the struct structural-eq seed — two identical array types
(module
  (type $a1 (array (mut i32)))
  (type $a2 (array (mut i32)))
  (func (export "f") (result i32)
    (ref.test (ref $a1) (array.new_default $a1 (i32.const 1)))))
