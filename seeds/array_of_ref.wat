;; array whose element is a struct ref — probes covariance + nullability of element type.
(module
  (rec
    (type $s (sub (struct (field i32))))
    (type $a (sub (array (ref $s)))))
  (func (export "f") (result i32)
    (struct.get $s 0
      (array.get $a (array.new $a (struct.new $s (i32.const 44)) (i32.const 1)) (i32.const 0)))))
