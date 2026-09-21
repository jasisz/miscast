# Findings

Soundness and conformance divergences miscast has surfaced in WebAssembly GC interpreters, each verified
against the strongest agreeing oracle set available for that case: the WebAssembly **reference interpreter**,
the generated self-checking oracle, and/or production engines such as V8, wasmtime and WasmEdge.

The short index lives in the [README](../README.md#found-in-the-wild); this is the long form.

---

## WAMR

Tested on current `main` at `b70d708d46be750bfcf008218b42c7b98c49368a` with both the classic and fast
interpreters, each built with ASan+UBSan and GC enabled. The modules validate with `wasm-tools`; Wasmtime
supplies the agreeing execution oracle.

### `array.fill` / `array.copy` skip bounds checks when the length is zero — [WAMR#5113](https://github.com/wasm-micro-runtime/wasm-micro-runtime/issues/5113)

The GC instructions still have to validate their start offsets when the requested length is zero: an access
is in bounds only when `offset + length <= array length`. WAMR wraps both the bounds check and the operation
in `if (len > 0)`, so an offset beyond the end is silently accepted whenever `len == 0`.

```wat
(module (type $a (array (mut i32)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 4
      (i32.const 10) (i32.const 11) (i32.const 12) (i32.const 13)))
    (array.fill $a (local.get $a) (i32.const 5) (i32.const 77) (i32.const 0))
    (array.get $a (local.get $a) (i32.const 3))))
```

WAMR returns `13`; Wasmtime traps with `out of bounds array access`. The same defect accepts
`array.fill` at `0xffffffff` and `array.copy` with source and destination offsets `0xffffffff`, all with
length zero. Exact-end controls (`offset == 4`, length zero) run correctly. Found by `arraywrap`; the shared
cause is visible in both interpreter dispatch loops, including the
[`if (len > 0)` around `array.fill`](https://github.com/bytecodealliance/wasm-micro-runtime/blob/b70d708d46be750bfcf008218b42c7b98c49368a/core/iwasm/interpreter/wasm_interp_fast.c#L2563-L2573)
and the equivalent guard around `array.copy`.

### `array.new_data` reads a data segment after `data.drop` — [WAMR#5114](https://github.com/wasm-micro-runtime/wasm-micro-runtime/issues/5114)

After `data.drop`, the segment behaves as a zero-length segment. A request for one element at offset zero
must therefore trap, but WAMR reads the original retained bytes and constructs the array:

```wat
(module (type $a (array (mut i32)))
  (data $d "\01\02\03\04")
  (func (export "f") (result i32)
    (data.drop $d)
    (array.len (array.new_data $a $d (i32.const 0) (i32.const 1)))))
```

WAMR returns `1`; Wasmtime traps. Both interpreter implementations consult the segment's original
`data_length` and bytes but never the instance's dropped-segment bitmap (the fast path starts
[`WASM_OP_ARRAY_NEW_DATA` here](https://github.com/bytecodealliance/wasm-micro-runtime/blob/b70d708d46be750bfcf008218b42c7b98c49368a/core/iwasm/interpreter/wasm_interp_fast.c#L2351-L2415)).
Found by `arrayops`. This is a Wasm semantic soundness bug, not an ASan-detected host use-after-free: WAMR
retains the segment allocation and incorrectly keeps it semantically accessible.

---

## WasmEdge

### Validator accepts a forward supertype reference — [WasmEdge#5061](https://github.com/WasmEdge/WasmEdge/issues/5061)

WasmEdge's validator accepts a module with a **forward supertype reference** — a `sub` type whose declared
supertype has a *larger* type index — which the spec rejects (a supertype must be defined before the type
that extends it). Found by the `mutate` type-graph generator (`reorder-rec`), **not** the corpus — this
module isn't in the testsuite. Live in 0.17.0, in both the interpreter and the AOT compiler.

---

## Talos (a Lean wasm interpreter)

### `call_indirect` accepts a supertype — [talos#95](https://github.com/cajal-technologies/talos/issues/95) *(fixed)*

`call_indirect` accepted a *supertype* where the spec requires a *subtype*, running an ill-typed indirect
call. Reproduced straight from the spec's own `gc-type-subtyping.wast`, with no mutation and no hand-seed:
the spec's `(assert_trap (invoke "failN") "indirect call type mismatch")` cases *ran* instead of trapping,
while V8, wasmtime, the reference interpreter and the spec's own assert all trapped. Reported and since
fixed.

### `call_indirect` across reordered recursion groups — [talos#108](https://github.com/cajal-technologies/talos/issues/108)

Talos computes recursion-group type identity **equi-recursively** (by unrolling / bisimulation) rather than
**iso-recursively** (by positional rec-group structure). Two recursion groups holding the same
mutually-recursive types in a different **member order** are distinct types under the spec's canonicalization
(canonicalization substitutes positional / de-Bruijn recursive indices, so the canonical form depends on
member order), so a `call_indirect` against one type on a function of the other must trap. Talos treats them
as equal and executes the ill-typed call.

```wat
(module
  (rec (type $A1 (func (param (ref null $B1)) (result i32))) (type $B1 (struct (field (ref null $A1)))))
  (rec (type $B2 (struct (field (ref null $A2)))) (type $A2 (func (param (ref null $B2)) (result i32))))
  (func $f (type $A1) i32.const 77)
  (table 1 funcref) (elem (i32.const 0) $f)
  (func (export "go") (result i32) (call_indirect (type $A2) (ref.null $B2) (i32.const 0))))
```

wasmtime, WasmEdge and V8 trap (`indirect call type mismatch`); Talos returns `77`. Controls: identical
order → all agree; a func-only mutually-recursive group → still fires; unrelated types → Talos correctly
traps — isolating it to member-order canonicalization. Found by the `recgroup` mode.

### Global init rejects a plain `struct.new` but accepts one wrapped in extended-const arithmetic — [talos#109](https://github.com/cajal-technologies/talos/issues/109)

A global initializer accepts a GC constant expression only when it is wrapped in extended-const arithmetic,
and **rejects the plainer leaf form** — the simpler, more obviously-valid expression is the one refused.

| global init | wasm-tools | wasmtime | WasmEdge | V8 | Talos |
|---|---|---|---|---|---|
| `struct.new $s (i32.const 100)` | VALID | 100 | 100 | 100 | **`error: global init expression must be i32.const or i64.const`** |
| `struct.new $s (i32.add (i32.const 50) (i32.const 50))` | VALID | 100 | 100 | 100 | `100` |

Both are valid GC / extended-const constant expressions. The error message suggests the global-init check
only allows `i32.const` / `i64.const` at the top level, but the arithmetic-wrapped form slips past it.

---

## wasmz (a Zig wasm interpreter with GC)

### `i31ref` / `funcref` global instantiation panic — [wasmz#4](https://github.com/Ray-D-Song/wasmz/issues/4)

A valid module whose only content is an `i31ref` global initialized by `ref.i31` (a constant expression
that needs no defined struct/array type) panics at instantiation with `reached unreachable code`, where
wasm-tools, wasmtime and the reference interpreter all accept and run it. A plain `funcref` global hits the
same path. Found by the `mutate` type-graph generator over the GC corpus.

### `ref.test` / `ref.cast` against a concrete function type under-matches — [wasmz#5](https://github.com/Ray-D-Song/wasmz/issues/5)

`ref.test` / `ref.cast` against a concrete function type never matches a non-null funcref (it matches only
the abstract `func` heap type), so `ref.test (ref $ft) (ref.func $fa)` returns 0 where the funcref is
exactly `$ft`; wasm-tools, wasmtime, WasmEdge and V8 all return 1, differing by a single byte (the heap-type
immediate). Found by the shadow-GC oracle (`morphism`, the funcref fold step) and reduced to that one-byte
differential.

### Struct type identity is nominal, not structural — [wasmz#6](https://github.com/Ray-D-Song/wasmz/issues/6)

`ref.test` / `ref.cast` compares struct type identity **nominally** (by declared type index) instead of
**structurally**: two separately-declared, non-final, structurally-identical struct types are the same type
under iso-recursive canonicalization, so `ref.test (ref $a)` on a `struct.new $b` value must be 1, but wasmz
returns 0; wasmtime, WasmEdge and V8 all return 1. A finality control (make one type `final`) collapses all
engines to 0, isolating the defect to non-final structural canonicalization. Found by the shadow-GC oracle
(`morphism`, the `bit2` canonicalization rail), distinct from #5. (The same nominal-identity defect
reappears on arrays, separate rec groups, subtype chains, `br_on_cast` and `call_indirect` — the root is one
bug across surfaces.)

### `br_on_cast` / `br_on_cast_fail` forwards null to the taken branch — [wasmz#7](https://github.com/Ray-D-Song/wasmz/issues/7)

When `br_on_cast` takes its branch (the cast succeeds), the spec forwards the operand — now typed as the cast
target — to the branch label. wasmz keeps the control flow correct (the branch is taken) but forwards a
**null** reference instead, silently corrupting the value.

```wat
(module
  (type $s (struct (field i32)))
  (func (export "f") (result i32)
    (block $hit (result (ref $s))
      (br_on_cast $hit anyref (ref $s) (struct.new $s (i32.const 42)))
      (unreachable))
    (ref.is_null)))    ;; forwarded ref is non-null -> expected 0; wasmz returns 1
```

`ref.is_null` → 0 expected / wasmz 1; `ref.test (ref $s)` → 1 / 0; `struct.get $s 0` → 42 / trap (null
dereference). wasmtime, WasmEdge, V8 and Talos all correct. Found by the `compose` mode (the `br_on_cast` conduit + a `ref.is_null` probe).

### An array reference thrown as an exception tag parameter is corrupted after catch — [wasmz#9](https://github.com/Ray-D-Song/wasmz/issues/9)

A GC **array** reference (`ref $arr`) used as a `throw` tag parameter is corrupted once the handler catches
it: a subsequent `array.get` reads garbage instead of the stored element, and some indices fault the host
(`Unable to dump stack trace`) — a memory-safety escape, not just a wrong value.

```wat
(module
  (type $arr (array (mut i32)))
  (tag $e (param (ref $arr)))
  (func (export "f") (result i32)
    (block $h (result (ref $arr))
      (try_table (result i32) (catch $e $h)
        (array.new_fixed $arr 3 (i32.const 4) (i32.const 8) (i32.const 15))
        (throw $e)
        (unreachable))
      (return))
    (array.get $arr (i32.const 2))))
```

wasmz returns `524288`; wasmtime, V8 and the reference interpreter return `15`. Controls isolate it to array
references forwarded through a tag: a **struct** reference forwarded the same way reads its field back
correctly (`31`), and a plain `array.get` outside any exception returns `15`. Found by the `compose` mode (the exception-tag conduit)
(`try_table` / `throw` / `exnref` self-checks), distinct from the absent-validator gap in #8.

### memory64 addresses are truncated to 32 bits before the bounds check — [wasmz#10](https://github.com/Ray-D-Song/wasmz/issues/10)

A memory64 (`i64`-indexed) linear-memory address that is out of bounds is **truncated to its low 32 bits
before the bounds check**, so a high address aliases `addr mod 2^32` and the load / store succeeds — reading
or **writing** memory the program cannot legally reach — instead of trapping. A memory-safety / sandbox
escape, not a wrong value.

```wat
(module (memory i64 1)
  (func (export "f") (result i32)
    (i32.store (i64.const 0) (i32.const 1431655765))   ;; sentinel 0x55555555 at offset 0
    (i32.load (i64.const 4294967296))))                ;; 2^32 — out of bounds, must trap
```

wasmtime, V8, the reference interpreter, WasmEdge and Wizard all trap (`out of bounds memory access`); wasmz
returns the aliased sentinel `1431655765`. Controls: a store at `2^32` then a load of offset `0` returns the
stored value (a **write** escape); a value at offset `8` is read back via address `2^32 + 8` (the address is
`addr & 0xffffffff`); an address `131072` (`< 2^32`, still OOB) traps correctly — so the 32-bit bounds check
works and only the **high 32 bits are dropped**. Found by the `memory64` mode.

### Effective address `offset + addr` wraps in 32 bits — an OOB access aliases the bottom of a 32-bit memory — [wasmz#12](https://github.com/Ray-D-Song/wasmz/issues/12) *(fixed on main before the report — see the postscript)*

The memarg effective address is folded in 32 bits, so an offset near 2³² wraps the access back to the bottom
of the memory instead of trapping — on **plain 32-bit memories**, no memory64 needed:

```wat
(module (memory 1)
  (func (export "f") (result i32)
    (i32.store (i32.const 0) (i32.const 0x5A5A5A5A))   ;; sentinel at 0
    (i32.load offset=0xFFFFFFFF (i32.const 1))))        ;; ea = 0x100000000 — must trap
```

wasmz returns the sentinel `1515870810` (`0x5A5A5A5A`) — every oracle traps. The
**write** variant is worse: an `i32.store offset=0xFFFFFFFF (i32.const 1)` goes through and a read-back of
offset 0 returns the stored value, so an out-of-bounds store *lands* at `ea mod 2³²`. Five wrap shapes
(`0xFFFFFFFF+1`, `0xFFFFFFF0+0x10`, `0x80000000+0x80000001`, `0x20+0xFFFFFF00`, `0xFFFFFFF8+0xC`) all
execute; 21 control programs (plain last-byte boundaries, per-width 8/16/32/64 last-slot sweeps,
large-but-legal offsets in a 2-page memory, addr-only OOB) all behave exactly right — so the bounds check
itself is precise and only the `offset + addr` addition wraps. The same truncation disease as the fixed
wasmz#10 (there the memory64 address, here the memarg offset on ordinary memories): the spec computes `ea`
exactly — it can need 33 bits — and mandates the trap. Found by the `memarg` mode; wasmtime, V8, WasmEdge,
mcr, Talos and Wizard return the exact expected verdicts on all 33.

### Spec-invalid modules are accepted and run — [wasmz#8](https://github.com/Ray-D-Song/wasmz/issues/8)

`wasmz module.wasm f` loads and runs to completion modules that every conformant validator rejects, returning
a value where the others reject at load — no validation of **type-section subtyping** (a subtype that
retypes / drops a field, extends a `final` type) or **operand-stack typing**
(a block / function result of the wrong type or arity, a non-defaultable `array.new_default`). Each module is
rejected by `wasm-tools validate` (ground truth); accepting them means ill-typed code runs. Found by the
`invalid` battery.

---

## Wizard (Ben Titzer's reference engine, in Virgil)

Wizard is conformant across the spec corpus and every generated probe — including the deep adversarial sweep
(structural canonicalization, packed fields, collector torture, the runtime cast/branch surface) — except
two gaps in an otherwise-strong validator: it correctly rejects every subtype-rule and operand-stack
violation, but misses two reference-type cast checks.

### `ref.test` / `ref.cast` accept a target heap type from a different hierarchy — [wizard#654](https://github.com/titzer/wizard-engine/issues/654)

`ref.test` / `ref.cast` validate (and run) with a target heap type in a *different* type hierarchy than the
operand — e.g. `ref.test (ref extern)` / `ref.cast (ref extern)` on an `(ref i31)` value. The spec requires
the target and operand to share a top (`any` / `func` / `extern`); these are ill-typed and wasm-tools, V8 and
wasmtime reject them, but Wizard accepts and runs them (and `ref.test (ref extern)` on an i31 even returns 1,
claiming the i31 is an extern). `br_on_cast` / `br_on_cast_fail` correctly reject the same cross-hierarchy, so
the gap is localized to the `ref.test` / `ref.cast` pair. Found by the `mutate` type-graph generator and a
deep adversarial sweep.

### `br_on_cast` / `br_on_cast_fail` accept a target label with an empty result type — [wizard#655](https://github.com/titzer/wizard-engine/issues/655)

`br_on_cast` forwards the operand to its target label, so the label's type must be non-empty (end in a
reference type). Wizard does not reject an empty (arity-0) target label; it accepts and runs the module. A
wrong-typed but non-empty label (`i32`) is correctly rejected, isolating the gap to the empty-label / arity
requirement. Both `br_on_cast` and `br_on_cast_fail`. Found by a deep adversarial sweep, then folded into
the `invalid` battery.

### `array.copy` checks element-type subtyping in the wrong direction — [wizard#656](https://github.com/titzer/wizard-engine/issues/656)

`array.copy` requires the **source** array's element type to be a **subtype** of the **destination**'s, so
every copied element is assignable to the destination. Wizard checks it backwards: it *rejects* a valid
widening copy (src elem `<:` dst elem) and *accepts and runs* an invalid narrowing copy (src elem `:>` dst
elem). The narrowing case is a type confusion — it writes supertype-typed values into a subtype-typed array,
so a later read at the subtype reads a wrong-typed object; with a wider subtype it reads past the object and
**faults the host** with an uncaught `java.lang.ArrayIndexOutOfBoundsException` (the JVM target's bounds
check catches the out-of-bounds — a host crash, not a Wasm trap, and not memory corruption). This is the
strongest finding by severity class (type confusion → host crash), tempered by it being a contained crash in
a research engine. Found by the goblin feature-interaction sweep (GC × bulk-array ops).

### `array.new_data` / `array.new_elem` trap on a zero-length access of a dropped segment — [wizard#657](https://github.com/titzer/wizard-engine/issues/657)

A passive data / element segment that has been `data.drop` / `elem.drop`'d has length 0, so an
`array.new_data` / `array.new_elem` with offset 0 and size 0 is in bounds (`0 + 0 <= 0`) and must produce a
zero-length array. Wizard traps `MEMORY_OOB` instead (a completeness over-trap). Found by the goblin sweep
(GC × segments).

> The two validator gaps (#654, #655) and the cast/segment bugs all reproduce on Talos and wasmz as well, but
> there they are symptoms of an absent validator (Talos's runner does not validate on load and its operand-stack
> type checker is admittedly future work; wasmz [#8](https://github.com/Ray-D-Song/wasmz/issues/8)) rather than
> distinct bugs — Wizard is the notable case because its validator otherwise rejects everything else, so the
> reversed `array.copy` check is a specific correctness bug, not a missing feature. The `array.copy` narrowing
> depth-limit probe is kept for compatibility tracking, not as a standalone upstream report. The `array.copy`
> narrowing reaches all three but the consequence differs: **wasmz** reads out-of-bounds (`0xAAAAAAAA`, a type-confusion
> memory disclosure — the most concrete; noted on wasmz #8), **Wizard** faults the host (a JVM
> `ArrayIndexOutOfBoundsException`), and **Talos**'s runtime field-bounds check contains it (a clean error). V8,
> wasmtime and WasmEdge reject all of it. (A reported `i31.get_s` "sign-extension" divergence turned out to be a
> signed/unsigned *display* difference, identical bits — not a bug.)
