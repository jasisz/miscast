;; struct subtype with a mutable field — probes field-mut variance + sub edges.
(module
  (rec
    (type $base (sub (struct (field (mut i32)))))
    (type $derived (sub $base (struct (field (mut i32)) (field i32)))))
  (func (export "f") (result i32)
    (struct.get $base 0 (struct.new $derived (i32.const 11) (i32.const 22)))))
