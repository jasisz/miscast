;; reason: br_on_cast targets a label with an empty (arity-0) result type
(module (type $s (struct (field i32))) (func (export "f") (result i32) (block $l (br_on_cast $l anyref (ref $s) (struct.new $s (i32.const 42))) (drop) (return (i32.const 111))) (i32.const 222)))
