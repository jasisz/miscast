# miscast — a differential & self-checking tester for WebAssembly GC soundness

Replays the official WebAssembly spec testsuite against any interpreter, running
each case on several engines: V8, wasmtime, and the WebAssembly **reference
interpreter**. The reference interpreter is treated as the spec oracle when
available; V8 and wasmtime add production-engine corroboration. A divergence is
reported only when the selected oracles **agree** — above all a **SOUNDNESS**
divergence, where the system-under-test runs something every oracle traps on, or
accepts a module every oracle rejects. The spec authors already wrote the hard
ill-typed cases; curated hand-seeds cover the corners the testsuite misses.

It also **generates self-checking** GC-soundness programs that carry their own oracle
— a shadow-GC model, rec-group canonicalization, extern-convert round-trips,
`br_on_cast` value-forwarding, exception-handling unwinding (the `morphism` / `recgroup`
/ `externconvert` / `castbr` / `eh` / `exnstack` / `compose` modes) — so a single engine's wrong answer is a
self-evident bug with no second engine to consult; and a curated battery of spec-invalid
modules (`invalid`) that every conformant validator rejects. These found the
maturing-interpreter bugs below.

*The name:* it catches an interpreter that **mis-casts** — runs ill-typed code (a
botched subtype/cast check) a conformant engine would reject.

```
python3 -m miscast --sut ENGINE [--mode replay|mutate|smith|morphism|recgroup|externconvert|castbr|eh|exnstack|compose|invalid|all] [--oracles LIST] [--seeds DIR] [-n N] [--overtrap]
```

**No third-party dependencies** — only the Python standard library. The one external
tool it requires is `wasm-tools`; the engines are optional and auto-detected: `node`
(≥22, for the V8 oracle), `wasmtime`, and the reference interpreter (via `SPEC_WASM`).
Run from the repo root. The code is a small package (`miscast/`): `config` /
`toolchain` / `engines` / `verdict` / `runner` / `mutate` / `reify` / `morphism` /
`recgroup` / `externconvert` / `castbr` / `eh` / `exnstack` / `compose` / `invalid` / `wast` / `reduce` / `modes` / `repro` /
`cli`, plus an optional Rust embedder in `runner/`.

## Native input: `.wast`

`.wast` is the official wasm script format: a module plus how to exercise it —
`(assert_return (invoke "fn" args) result)`, `(assert_trap (invoke "fn") "...")`,
`(assert_invalid (module ...) "...")`. `replay` parses each file into its **ordered
command stream** and runs three differential sections over it:

- **execution** — per-action value / trap differential. Independent actions run on
  every engine and their results are compared; the assert is the spec author's own
  expected result, a corroborating oracle.
- **validation** — `assert_invalid`: a module the spec marks ill-typed. Every oracle
  must **reject** it; a SUT that accepts (or runs) it is unsound — the direction a
  value/trap differential structurally can't see.
- **conformance** — a file with **stateful** segments (several invokes that mutate
  global / table / memory state) is run as the *whole real* `.wast` file, natively and
  in order on the reference interpreter (`wasm file.wast`), state preserved — not
  shredded into fresh-per-invoke cases. A SUT that can execute a `.wast` script
  (`CUSTOM_WAST_CMD`) is compared against it.

A bare `.wat` is accepted too — treated as one module exercised by `(invoke "f")`.

So the tool consumes two corpora in the same format:
- **`seeds/`** — our hand-written `.wat` probes, one subtyping corner each.
- **`spec/wast/`** — the official WebAssembly **core** testsuite (~114 files, GC
  included). `./spec/fetch.sh` pulls it; the GC subdir files land under a `gc-` prefix
  (`gc-type-subtyping`, `gc-ref_test`, `gc-ref_cast`, `gc-br_on_cast`, `gc-struct`,
  `gc-array`, `gc-i31`, …).

## Engines (auto-detected; `--sut` picks the one under test, the rest are oracles)

| engine     | how                                                                   |
|------------|-----------------------------------------------------------------------|
| `v8`       | Node ≥22 (WasmGC) + bundled `oracle/v8.js`                            |
| `wasmtime` | `wasmtime run --invoke` (execution) · `wasmtime compile` (validation); conformance: n/a (see Honest limits) |
| `mcr`      | the `runner/` wasmtime-crate embedder (`cargo build --release` in `runner/`): **exact result bits**, a **gc-capable** wasmtime even when the PATH `wasmtime` was built without the gc feature, and stateful `--seq`. Auto-detected when built; path overridable via `MC_RUNNER` |
| `spec`     | the WebAssembly **reference interpreter** (the spec oracle), via env `SPEC_WASM=/path/to/wasm` |
| `custom`   | **your** interpreter, via env `CUSTOM_CMD="cmd {wat} {wasm} {export}"` — no code change |

`--sut` is required — there is no privileged default engine; you say what is under
test. The oracles (the assert + the other engines) must **agree** before a
divergence is blamed on the SUT; disagreement → `oracle-split`, a confounder,
never a finding. A SOUNDNESS verdict means the SUT runs while *every* oracle traps,
or accepts a module *every* oracle rejects.

By default every other detected engine is an oracle; `--oracles v8` restricts to a
chosen subset (the SUT is always run alongside) — so you pick *which* oracles and
*how many* (`--oracles v8` for a quick one-oracle pass, or wire a reference
interpreter via `SPEC_WASM` and use `--oracles spec` to check against the spec oracle alone).

By default **soundness**, **value**, and **crash** divergences are counted. A *completeness*
divergence (the SUT traps where the oracles run) is the SUT's own concern — often just an
unmodeled instruction — so it is tallied as `over-traps` but flagged only with `--overtrap`.
A **crash** is distinct from a Wasm trap: a clean `trap` is defined behaviour, but a native
segfault / Rust panic / uncaught host exception means the *engine itself fell over* on the input —
a stronger signal, so it is its own class (it surfaced a Wizard `array.copy` type confusion that
faults the host with an `ArrayIndexOutOfBoundsException`, where a plain UNSUP would have hidden it).

## Modes

| mode     | what it does                                                                   |
|----------|--------------------------------------------------------------------------------|
| `replay` | run the file's command stream — three sections: **execution** (value/trap differential, assert as oracle), **validation** (`assert_invalid`), **conformance** (stateful scripts run natively, state preserved) |
| `mutate` | break a **type relationship** in each seed — re-point an op's type slot, drop / flip a supertype edge, reorder a rec group, toggle ref nullability or `final` — then route each variant by validity: valid ones to the execution differential, **ill-typed ones to the validation differential** (does the SUT *reject* them?). A SUT that accepts a generated ill-typed module is unsound. |
| `smith`  | random valid GC modules via `wasm-tools smith` — breadth baseline / drop-in    |
| `morphism` | self-checking **shadow-GC** programs: run one object graph through **several** real Wasm GC representations *and* a hand-rolled linear-memory model, check they all agree, and **isolate** which representation path diverges (see below) |
| `recgroup` | **rec-group canonicalization** trap-differential: two recursion groups holding the same mutually-recursive types in different **member order** are distinct types under iso-recursive canonicalization, so a `call_indirect` against one on a function of the other must **trap**. A SUT that runs it canonicalizes equi-recursively and executed an ill-typed call (found Talos#108). |
| `externconvert` | **`extern.convert_any` / `any.convert_extern`** round-trip: a GC ref pushed out to `externref` and back must be preserved, so each program self-checks by returning the round-tripped value. A SUT that traps or returns something else diverges (an engine missing the conversion opcodes). |
| `castbr` | **`br_on_cast` / `br_on_cast_fail`** value-forwarding: the cast operand — not null — must reach the branch, so each program reads the forwarded reference back (`ref.is_null` / `ref.test` / a field) and self-checks. A SUT that forwards a null returns the wrong value (or traps on the field read). |
| `eh` | **exception-handling** self-checks (`try_table` / `throw` / `throw_ref` / `exnref`): each program returns a sentinel only the conformant unwinding + tag/`exnref` forwarding produces (or traps where a null `throw_ref` must) — plain / multi-param / `catch_all` / `catch_ref` / `catch_all_ref` catches, an `exnref` captured into a local / GC struct field / array element / passed across a call frame, `throw_ref` re-raising with its payload intact, propagation past a non-matching handler, deep multi-frame unwinds, and a GC reference forwarded through a tag. The per-program oracle is baked in, so one engine's wrong answer is self-evident (found a wasmz array-ref corruption, #9). The spec-invalid EH modules (catch / throw arity + type rules) live in the `invalid` battery. |
| `exnstack` | **generated** `try_table` unwind trees where the **Python unwind simulator is the oracle**: because exception routing is statically decidable, the generator bakes the exact result, so each program self-checks at an unwind depth and routing complexity the hand-written `eh` fixtures can't reach. A nest of handlers with selective tag matching catches an innermost `throw` carrying a GC payload swept across **kind** (a mutable array read at a non-zero index, a two-field struct, an `i31`, or a struct nesting an array) and **consume pattern** (read directly off the catch-forwarded value, or first stored to a local); each handler adds `level*100000`, so a **mis-routed** throw (caught by the wrong handler) and a **corrupted reference** carried across the unwind both surface as a wrong value. It pins the wasmz array-ref-through-tag corruption to **array-element reads specifically** — it fires on a bare array and on an array nested in a struct (even via a local), but spares pure struct-field and `i31` reads — while Talos, WasmEdge and Wizard route and preserve every kind cleanly. |
| `compose` | a **compositional feature-interaction generator**: every soundness scalp lives at an interaction (exceptions × GC, `array.copy` × GC, `br_on_cast` × forwarding), so this factors the siloed modes into four orthogonal **mechanisms** and recombines them. A **payload** (array element / struct field / `i31` / array nested in a struct) is threaded through a random **MIX of 1–3 conduits** — an exception tag (throw→catch), a function call, a mutable global (store→load), a `br_on_cast` branch, a **tail call** (frame replacement), a GC **struct field** (store→load), a **table** slot, or an **extern** round-trip — optionally **stressed** by a forced GC, and read **direct off the stack or via a local**. It stays self-checking by one law: *a value carried through a value-preserving conduit must come out intact*, so the oracle is just the payload value through any composition. Emits interactions no single mode does; the recipe in the case name is the reproducer. (It pinned the wasmz array corruption to **branch-target reference forwarding** — it fires through `br_on_cast` and an exception catch, the two conduits that `br` the operand to a label, but not through a call or a global — unifying it with the `br_on_cast` null-forward bug.) |
| `invalid` | a curated battery of **spec-invalid GC modules** (`corpus/invalid/*.wat`, one per case) routed to the validation differential — type-section subtyping (narrow / drop / retype a field, extend a `final` type, exceed depth 63), operand-stack typing (wrong block / function result type or arity, non-defaultable `array.new_default`), and reference-type casts (a `ref.test` / `ref.cast` whose target heap type is in a different hierarchy than the operand, a `br_on_cast` / `br_on_cast_fail` whose target label cannot receive the forwarded operand). Every conformant validator rejects them; a SUT that **accepts and runs** one has no validator for that rule and is unsound. Add a case by dropping a `.wat` into the corpus. |
| `all` | run the whole **self-checking soundness oracle suite** (`morphism` + `recgroup` + `externconvert` + `castbr` + `eh` + `exnstack`) **and** the `invalid` validation battery in one command — no corpus needed, each execution program is its own oracle, and a finding's case name says which probe fired. |

## The shadow-GC oracle (`--mode morphism`)

A GC reference returned by a function is opaque, so the value-differential can only compare it by status —
blind to the wrong-typed object an unsound cast hands back. `reify` (below) turns that reference into a
scalar fingerprint; the **shadow-GC oracle** goes the whole way: it runs an entire object graph through
real Wasm GC **and** through a hand-rolled model of the same graph in plain linear memory, then checks they
agree. The linear-memory shadow cannot be wrong about GC because it uses no GC, so a single engine
disagreeing with it is a self-evident bug — no second engine required. A known-correct engine agreeing also
confirms the worlds are genuinely equivalent, so a divergence elsewhere is that engine's defect, not a
generator artifact.

One random program is emitted from a single data-segment "tape", so every world is equivalent by
construction. To not just *detect* a divergence but *isolate* which operation caused it, the graph is
realized as **three** real GC representations alongside the shadow, all folding the same rolling checksum
(ids, subtype-test outcomes, the right field, `ref.eq` identity, the followed reference's id, a
funcref-type test, an `i31` value):

- **cast** rail — type membership via `ref.test` / `ref.cast` against the declared type (the ordinary way),
  over a `$base` / `$sub` / `$sub2` hierarchy with `struct.new` / `set` / `get`, churn that forces a
  collection, and a post-GC field mutation;
- **tag** rail — the same graph, but **cast-free**: membership comes from the tape tag and subtype fields
  are read through per-kind typed arrays, so it never executes a `ref.test` / `ref.cast`. It is the rail a
  cast bug *cannot* touch, so it stays equal to the shadow exactly when the cast path is broken;
- **shard** rail — identical to cast except the sub-test uses `$subB`, a **separately declared** type that
  is **structurally identical** to `$sub`. Under iso-recursive canonicalization they are the same type, so
  a conformant engine must agree; an engine that uses nominal (declaration) identity diverges only here.

The exported `check` returns a bitmask, so a nonzero result also names the failing class: `bit0` cast ≠
shadow (a funcref / own-type `ref.test`, a write barrier, relocation under a moving collector, or any value
bug on the cast path), `bit1` tag ≠ shadow (GC storage / identity — the cast-free rail moved), `bit2` shard
≠ cast (type **canonicalization** — `$sub` and its structurally-identical twin disagree). **0** means every
rail agrees. `wasmtime`, `WasmEdge` and `V8` return 0 on every program; the maturing interpreters light up —
`Talos` returns `5` (`bit0` funcref **and** `bit2` canonicalization, two distinct defects isolated in one
run). The check even caught a wrong assumption about structural type canonicalization while it was being
built, and it found the funcref subtype-check bug below (`ref.test (ref $ft)` on a concrete funcref).

## Two ways the bug shows up

```
# the spec already wrote the ill-typed cases — replay surfaces them directly:
./spec/fetch.sh
CUSTOM_CMD="/path/to/talos/runner {wat} {export}" \
  python3 -m miscast --mode replay --seeds spec/wast --sut custom

# our seeds are valid; mutation constructs the ill-typed variants:
CUSTOM_CMD="..." python3 -m miscast --mode mutate --seeds seeds --sut custom
```

Replaying the official `gc-type-subtyping.wast` against the **Talos** Lean
interpreter surfaced its `call_indirect` soundness bug with **no mutation and no
hand-seed**: the spec's own `(assert_trap (invoke "failN") "indirect call type
mismatch")` cases *ran* instead of trapping — V8, wasmtime, the reference interpreter
and the spec's own assert all trapped, Talos returned. (From the outside the
`call_indirect` check looked like an exact structural signature compare rather than a
subtype check.) Reported as [Talos#95](https://github.com/cajal-technologies/talos/issues/95)
and since fixed; the same replay still surfaces the class on any unpatched engine, and the
related rec-group canonicalization variant (`recgroup` mode) remains open as
[Talos#108](https://github.com/cajal-technologies/talos/issues/108).

Trimmed output from that run (Talos as the SUT):

```
## execution — per-action value / trap differential
case                         v8     wasmtime  spec   custom  assert  verdict
gc-type-subtyping#31:fail1   TRAP   TRAP      TRAP   OK _    TRAP    SOUNDNESS  <<<
gc-type-subtyping#33:run     OK 1   OK 1      OK 1   OK 0    OK 1    VALUE      <<<
...
FINDINGS=19  SOUNDNESS=5  VALUE=14
```

Each finding also writes `work/repro/<case>/` — the module, a runnable `case.wast`,
and the exact command each engine ran.

## Why targeted, and how it differs from existing tools

miscast either **replays the spec's own subtyping corpus** (which already encodes
the ill-typed cases) or **deliberately constructs** them by mutating a seed's type
slot — and is **SUT-agnostic**. That is a different niche from every existing tool:

| tool | technique | module validity | level | GC | bug class |
|------|-----------|-----------------|-------|----|-----------|
| [Waltzz](https://github.com/mobsceneZ/Waltzz) (USENIX'25) | coverage-guided greybox | **preserves** (stack-invariant) | instruction / stack-type | no — "adheres to the established Wasm standard" | memory-safety / CVE |
| wasm-smith + [wasmtime diff-fuzz](https://github.com/bytecodealliance/wasmtime/blob/main/fuzz/README.md) | random from opaque bytes | preserves | instruction | yes | wrong-result — but can't build a subtyping mismatch ([#4322](https://github.com/bytecodealliance/wasmtime/issues/4322)) |
| wasm-mutate | mutation | **semantics-preserving** | encoding | maturing | compiler / optimizer |
| WADIFF / WASMaker / WRTester | random / symbolic / reassembly | preserves | bytecode / instruction | mostly pre-GC | runtime divergence |
| **miscast** | **differential + spec replay** | **deliberately violates** | **type relationships** | **GC subtyping** | **type-soundness** |

Every other tool either *preserves* validity (so it never constructs the ill-typed
subtyping case a soundness bug needs), can't handle GC, or targets a different bug
class. Even Waltzz — the 2025 state of the art — explicitly adheres to the
established standard (no GC) and hunts crashes, not type-soundness. miscast is a
**complement, not a replacement** for broad coverage-guided fuzzing: that finds a
huge range of bugs across the instruction set; this targets one corner (GC
subtyping soundness).

## Found in the wild

Maturing GC interpreters, each verified against the reference interpreter and the production engines
(V8, wasmtime, WasmEdge) as ground truth. Full write-ups — repros, controls, why each is a spec
violation — in [`docs/findings.md`](docs/findings.md).

| engine | divergence | found by | issue |
|--------|------------|----------|-------|
| WasmEdge | validator accepts a forward supertype reference | `mutate` | [#5061](https://github.com/WasmEdge/WasmEdge/issues/5061) |
| Talos | `call_indirect` accepts a supertype *(fixed)* | `replay` | [#95](https://github.com/cajal-technologies/talos/issues/95) |
| Talos | `call_indirect` across reordered recursion groups | `recgroup` | [#108](https://github.com/cajal-technologies/talos/issues/108) |
| Talos | global init rejects a plain `struct.new`, accepts an arith-wrapped one | probe | [#109](https://github.com/cajal-technologies/talos/issues/109) |
| wasmz | `i31ref` / `funcref` global instantiation panic | `mutate` | [#4](https://github.com/Ray-D-Song/wasmz/issues/4) |
| wasmz | `ref.test`/`ref.cast` vs a concrete function type under-matches | `morphism` | [#5](https://github.com/Ray-D-Song/wasmz/issues/5) |
| wasmz | struct type identity is nominal, not structural | `morphism` | [#6](https://github.com/Ray-D-Song/wasmz/issues/6) |
| wasmz | `br_on_cast` forwards null to the taken branch | `castbr` | [#7](https://github.com/Ray-D-Song/wasmz/issues/7) |
| wasmz | accepts spec-invalid modules (no validation) | `invalid` | [#8](https://github.com/Ray-D-Song/wasmz/issues/8) |
| wasmz | array ref thrown as an exception tag parameter is corrupted after catch | `eh` | [#9](https://github.com/Ray-D-Song/wasmz/issues/9) |
| Wizard | `ref.test` / `ref.cast` accept a cross-hierarchy target type | `mutate` | [#654](https://github.com/titzer/wizard-engine/issues/654) |
| Wizard | `br_on_cast` / `br_on_cast_fail` accept an empty-result target label | `invalid` | [#655](https://github.com/titzer/wizard-engine/issues/655) |
| Wizard | `array.copy` checks element-type subtyping backwards — narrowing copy → type confusion → host crash | `invalid` | [#656](https://github.com/titzer/wizard-engine/issues/656) |
| Wizard | `array.new_data` / `array.new_elem` over-trap on a zero-length access of a dropped segment | probe | [#657](https://github.com/titzer/wizard-engine/issues/657) |

Mature production engines (V8, wasmtime, WasmEdge) are conformant across the corpus and every generated
probe — the tool does not false-positive on them. Its edge is **maturing / research interpreters**.

## Honest limits

- A **GC reference** result is rewritten (in `mutate`/`smith`) to an **i32 fingerprint** of the
  reference's abstract type — `ref.is_null` + `ref.test` against i31/struct/array/eq + the i31 payload —
  so a subtype/cast unsoundness that returns a wrong-typed object shows as a value divergence instead of
  hiding behind a status-only compare. A single **f32/f64** result is rewritten to its reinterpreted
  integer bits and compared **bit-exactly** (catching sub-ULP miscompiles), with any NaN bit-pattern
  collapsed to one key. func/extern references and multi-value/v128 results stay status-only. The rewrite
  runs on every engine identically, so it can never manufacture a divergence.
- A run prints `N finding(s) in M distinct bug(s)`: dozens of mutation labels over one seed are the same
  underlying bug, so findings are deduped by base case + divergence shape (`reduce.py`), which also wraps
  `wasm-tools shrink` for minimizing a single reproducer.
- **Stateful** segments are run as the whole real `.wast` file, natively and in order
  on the reference interpreter, so state is preserved. (wasmtime's `wast` runner
  string-matches `assert_invalid` reason text and so fails official files merely for
  phrasing an error differently — too noisy to be a conformance oracle; V8 has no
  native `.wast` runner.) A one-shot SUT (an invoke-only runner) cannot execute a
  stateful script at all; those are reported `sut-stateful-na` — never run on a fresh
  instance and passed off as faithful. A SUT that *can* run a `.wast` script is wired
  via `CUSTOM_WAST_CMD="cmd {wast}"`.
- **Validation-differential** (`assert_invalid`) is implemented: every oracle
  (wasm-tools, the reference interpreter, wasmtime, V8) must reject the module. A SUT
  that exposes a real validate step is tested fully via `CUSTOM_VALIDATE_CMD`. An
  invoke-only SUT is tested where its interface allows — an invalid module that
  exports a runnable function is decisive (the SUT returns a value = accepted-and-ran =
  unsound); an exportless validator test, which a one-shot runner can't be driven on,
  is reported `sut-unsup`, never a false finding.
- `assert_malformed` (parser-level) and multi-module linking (`register`) are not
  modeled yet, and float/ref/v128 **arguments** can't be passed through a CLI invoke,
  so those actions are skipped (counted in the header).
- `smith` is random (counts vary run-to-run) and finds shallow over-traps, never the
  subtyping soundness class.

## Config (env, auto-detected otherwise)

| var          | default                                                          |
|--------------|------------------------------------------------------------------|
| `V8_ORACLE`  | bundled `oracle/v8.js`                                            |
| `NODE`       | newest WasmGC-capable node (≥22) on PATH or in `~/.nvm`          |
| `MC_RUNNER`  | path to the `mcr` embedder binary (default `runner/target/release/mc-runner`; build it with `cargo build --release` in `runner/`) |
| `SPEC_WASM`  | unset — path to the WebAssembly reference interpreter (`spec` oracle) |
| `CUSTOM_CMD` | unset — wire the interpreter under test as the `custom` engine    |
| `CUSTOM_NO_ARGS` | unset — set to `1` if the SUT's runner ignores per-call invoke arguments (e.g. an `--invoke` that always runs with zero args); arg-taking actions are then skipped (`SUT_NA`) instead of comparing a default-argument run against the oracles and mis-reporting a value divergence |
| `CUSTOM_VALIDATE_CMD` | unset — optional `cmd {wat} {wasm}` exposing the SUT's validate/load step (rc 0 = accepted) for full validation-differential coverage |
| `CUSTOM_WAST_CMD` | unset — optional `cmd {wast}` if the SUT can run a whole `.wast` script (rc 0 = conformant), for stateful conformance |

Findings are written to `work/divergences.txt`, with a self-contained reproducer per
finding under `work/repro/<case>/` (module, a runnable `case.wast`, and the exact
command each engine ran).
