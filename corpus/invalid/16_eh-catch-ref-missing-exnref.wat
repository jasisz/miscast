;; reason: a catch_ref label must carry the tag's params plus a trailing exnref ((i32 exnref) here), not just i32
(module
  (tag $e (param i32))
  (func (export "f") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch_ref $e $h)
        (i32.const 1)
        (return))
      (unreachable))))
