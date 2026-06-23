(module
  (type $s (struct (field i32)))
  (func (export "f") (result i32)
    (block $l (result (ref $s))
      (struct.new $s (i32.const 7))
      (br_on_cast $l (ref any) (ref $s))
      (unreachable))
    (struct.get $s 0)))
