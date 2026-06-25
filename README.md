# miscast — a `.wast`-native differential tester for WebAssembly GC subtype soundness

Replays the official WebAssembly spec testsuite against any interpreter, running
each case on several engines: V8, wasmtime, and the WebAssembly **reference
interpreter**. The reference interpreter is treated as the spec oracle when
available; V8 and wasmtime add production-engine corroboration. A divergence is
reported only when the selected oracles **agree** — above all a **SOUNDNESS**
divergence, where the system-under-test runs something every oracle traps on, or
accepts a module every oracle rejects. The spec authors already wrote the hard
ill-typed cases; curated hand-seeds cover the corners the testsuite misses.

*The name:* it catches an interpreter that **mis-casts** — runs ill-typed code (a
botched subtype/cast check) a conformant engine would reject.

```
python3 -m miscast --sut ENGINE [--oracles LIST] [--mode replay|mutate|smith] [--seeds DIR] [--overtrap]
```

**No third-party dependencies** — only the Python standard library. The one external
tool it requires is `wasm-tools`; the engines are optional and auto-detected: `node`
(≥22, for the V8 oracle), `wasmtime`, and the reference interpreter (via `SPEC_WASM`).
Run from the repo root. The code is a small package (`miscast/`): `config` /
`toolchain` / `engines` / `verdict` / `runner` / `mutate` / `reify` / `wast` /
`reduce` / `modes` / `repro` / `cli`, plus an optional Rust embedder in `runner/`.

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

By default only **soundness** and **value** divergences are counted. A
*completeness* divergence (the SUT traps where the oracles run) is the SUT's own
concern — often just an unmodeled instruction — so it is tallied as `over-traps`
but flagged only with `--overtrap`.

## Modes

| mode     | what it does                                                                   |
|----------|--------------------------------------------------------------------------------|
| `replay` | run the file's command stream — three sections: **execution** (value/trap differential, assert as oracle), **validation** (`assert_invalid`), **conformance** (stateful scripts run natively, state preserved) |
| `mutate` | break a **type relationship** in each seed — re-point an op's type slot, drop / flip a supertype edge, reorder a rec group, toggle ref nullability or `final` — then route each variant by validity: valid ones to the execution differential, **ill-typed ones to the validation differential** (does the SUT *reject* them?). A SUT that accepts a generated ill-typed module is unsound. |
| `smith`  | random valid GC modules via `wasm-tools smith` — breadth baseline / drop-in    |

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
interpreter reproduces its `call_indirect` soundness bug with **no mutation and no
hand-seed**: the spec's own `(assert_trap (invoke "failN") "indirect call type
mismatch")` cases *run* instead of trapping — V8, wasmtime, the reference interpreter
and the spec's own assert all trap, Talos returns. (From the outside the
`call_indirect` check looks like an exact structural signature compare rather than a
subtype check.)

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

Each verified against the reference interpreter as ground truth, on the latest engine version:

- **WasmEdge** — its validator accepts a module with a forward supertype reference (a `sub` type
  whose supertype has a larger type index), which the spec rejects. Found by the `mutate`
  type-graph generator (`reorder-rec`), **not** the corpus — this module isn't in the testsuite.
  Live in 0.17.0, in both the interpreter and the AOT compiler.
  [WasmEdge#5061](https://github.com/WasmEdge/WasmEdge/issues/5061)
- **Talos** (a Lean wasm interpreter) — `call_indirect` accepted a supertype where the spec
  requires a subtype, running an ill-typed indirect call. Reproduced straight from the spec's own
  `gc-type-subtyping.wast`, with no mutation and no hand-seed.
  [cajal-technologies/talos#95](https://github.com/cajal-technologies/talos/issues/95)
- **wasmz** (a Zig wasm interpreter with GC) — a valid module whose only content is an `i31ref`
  global initialized by `ref.i31` (a constant expression that needs no defined struct/array type)
  panics at instantiation with `reached unreachable code`, where wasm-tools, wasmtime and the
  reference interpreter all accept and run it. A plain `funcref` global hits the same path. Found by
  the `mutate` type-graph generator over the GC corpus.
  [Ray-D-Song/wasmz#4](https://github.com/Ray-D-Song/wasmz/issues/4)

Mature production engines (V8, wasmtime) are conformant across both the corpus and the generated
mutations — the tool does not false-positive on them. Its edge is **maturing / research
interpreters**, and (via `mutate`) **novel ill-typed modules** the big engines have not already fuzzed.

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
| `CUSTOM_VALIDATE_CMD` | unset — optional `cmd {wat} {wasm}` exposing the SUT's validate/load step (rc 0 = accepted) for full validation-differential coverage |
| `CUSTOM_WAST_CMD` | unset — optional `cmd {wast}` if the SUT can run a whole `.wast` script (rc 0 = conformant), for stateful conformance |

Findings are written to `work/divergences.txt`, with a self-contained reproducer per
finding under `work/repro/<case>/` (module, a runnable `case.wast`, and the exact
command each engine ran).
