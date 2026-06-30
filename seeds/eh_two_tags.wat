;; two exception tags with incompatible payloads — gives mutate throw/catch tag-use surface.
(module
  (tag $a (param i32))
  (tag $b (param i64))
  (func (export "f") (result i32)
    (block $ok (result i32)
      (try_table (result i32) (catch $a $ok)
        (throw $a (i32.const 33)))
      (unreachable))))
