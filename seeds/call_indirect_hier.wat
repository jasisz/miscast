;; call_indirect against a 3-level func hierarchy.
;; impl has the TOP type; mutate re-points the call at $top/$mid/$bot.
;;   $top  -> control (impl IS a subtype of the call type)  -> both run
;;   $mid/$bot -> impl is a SUPERtype of the call type -> engine TRAPs (ill-typed)
(module
  (rec
    (type $top (sub (func (param i32) (result i32))))
    (type $mid (sub $top (func (param i32) (result i32))))
    (type $bot (sub $mid (func (param i32) (result i32)))))
  (func $impl (type $top) (i32.add (local.get 0) (i32.const 1000)))
  (table 1 funcref)
  (elem (i32.const 0) $impl)
  (func (export "f") (result i32)
    (i32.const 7) (i32.const 0) (call_indirect (type $top))))
