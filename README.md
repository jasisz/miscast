# miscast — a `.wast`-native differential soundness tester for wasm interpreters

Replays the official WebAssembly spec testsuite against any interpreter, running
each case on several engines: V8, wasmtime, and the spec **reference interpreter**
as a gold-standard oracle. It flags where they diverge, above all **SOUNDNESS**
divergences, where the system-under-test runs something the oracles trap on. The
spec authors already wrote the hard ill-typed cases; curated hand-seeds cover the
corners the testsuite misses.

*The name:* it catches an interpreter that **mis-casts** — runs ill-typed code (a
botched subtype/cast check) a conformant engine would reject.

```
python3 -m miscast --sut ENGINE [--oracles LIST] [--mode replay|mutate|smith] [--seeds DIR] [--overtrap]
```

**No third-party dependencies** — only the Python standard library, plus the
external CLI tools `wasm-tools`, `node` (≥22, for the V8 oracle) and `wasmtime`.
Run from the repo root. The code is a small package (`miscast/`): `config` /
`toolchain` / `engines` / `verdict` / `runner` / `mutate` / `wast` / `modes` / `cli`.

## Native input: `.wast`

`.wast` is the official wasm script format: a module plus how to exercise it —
`(assert_return (invoke "fn" args) result)` / `(assert_trap (invoke "fn") "...")`.
Because the assert is the **spec author's own expected result**, for an unmutated
case it acts as a **third oracle** (conformance) alongside V8 and wasmtime. A bare
`.wat` is accepted too — treated as one module exercised by `(invoke "f")`.

So the tool consumes two corpora in the same format:
- **`seeds/`** — our hand-written `.wat` probes, one subtyping corner each.
- **`spec/wast/`** — the official WebAssembly GC testsuite. `./spec/fetch.sh` pulls
  it (type-subtyping, ref_test, ref_cast, br_on_cast, struct, array, i31, …).

## Engines (auto-detected; `--sut` picks the one under test, the rest are oracles)

| engine     | how                                                                   |
|------------|-----------------------------------------------------------------------|
| `v8`       | Node ≥22 (WasmGC) + bundled `oracle/v8.js`                            |
| `wasmtime` | `wasmtime run --invoke <fn>`                                          |
| `spec`     | the WebAssembly **reference interpreter** (gold-standard oracle), via env `SPEC_WASM=/path/to/wasm` |
| `custom`   | **your** interpreter, via env `CUSTOM_CMD="cmd {wat} {wasm} {export}"` — no code change |

`--sut` is required — there is no privileged default engine; you say what is under
test. The oracles (the assert + the other engines) must **agree** before a
divergence is blamed on the SUT; disagreement → `oracle-split`, a confounder,
never a finding. A SOUNDNESS verdict means the SUT runs while *every* oracle traps.

By default every other detected engine is an oracle; `--oracles v8` restricts to a
chosen subset (the SUT is always run alongside) — so you pick *which* oracles and
*how many* (`--oracles v8` for a quick one-oracle pass, or wire a reference
interpreter via `SPEC_WASM` and use `--oracles spec` for a gold-standard check).

By default only **soundness** and **value** divergences are counted. A
*completeness* divergence (the SUT traps where the oracles run) is the SUT's own
concern — often just an unmodeled instruction — so it is tallied as `over-traps`
but flagged only with `--overtrap`.

## Modes

| mode     | what it does                                                                   |
|----------|--------------------------------------------------------------------------------|
| `replay` | run each case as-is — the assert joins V8+wasmtime as an oracle (conformance + differential) |
| `mutate` | re-point a type slot in each module (`call_indirect` / `call_ref` / `array.*` / `struct.*` / `ref.test` / `ref.cast` / `br_on_cast`) at every declared **and** abstract type, sweeping the subtyping matrix — the assert no longer applies, so this is differential-only |
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

Replaying the official GC `type-subtyping.wast` against the **Talos** Lean
interpreter reproduces its `call_indirect` soundness bug with **no mutation and no
hand-seed**: the spec's own `(assert_trap (invoke "failN") "indirect call type
mismatch")` cases *run* instead of trapping — V8, wasmtime, and the assert all say
trap, Talos returns. (Reported upstream; root cause is `call_indirect` requiring an
*exact* signature match instead of subtyping.)

## Why targeted, and how it differs from existing tools

miscast either **replays the spec's own subtyping corpus** (which already encodes
the ill-typed cases) or **deliberately constructs** them by mutating a seed's type
slot — and is **SUT-agnostic**. That is a different niche from every existing tool:

| tool | technique | module validity | level | GC | bug class |
|------|-----------|-----------------|-------|----|-----------|
| [Waltzz](https://github.com/mobsceneZ/Waltzz) (USENIX'25) | coverage-guided greybox | **preserves** (stack-invariant) | instruction / stack-type | no — "adheres to the established Wasm standard" | memory-safety / CVE |
| wasm-smith + [wasmtime diff-fuzz](https://github.com/bytecodealliance/wasmtime/blob/main/fuzz/README.md) | random from opaque bytes | preserves | instruction | yes | wrong-result — but can't build a subtyping mismatch ([#4322](https://github.com/bytecodealliance/wasmtime/issues/4322)) |
| wasm-mutate | mutation | semantics-preserving | encoding | **can't even parse GC** | compiler / optimizer |
| WADIFF / WASMaker / WRTester | random / symbolic / reassembly | preserves | bytecode / instruction | mostly pre-GC | runtime divergence |
| **miscast** | **differential + spec replay** | **deliberately violates** | **type relationships** | **GC subtyping** | **type-soundness** |

Every other tool either *preserves* validity (so it never constructs the ill-typed
subtyping case a soundness bug needs), can't handle GC, or targets a different bug
class. Even Waltzz — the 2025 state of the art — explicitly adheres to the
established standard (no GC) and hunts crashes, not type-soundness. miscast is a
**complement, not a replacement** for broad coverage-guided fuzzing: that finds a
huge range of bugs across the instruction set; this targets one corner (GC
subtyping soundness).

## Honest limits

- A **reference** or void result prints differently per engine, so those cases are
  compared by **status only** (trap vs return), not by value — value comparison
  runs only when every engine returns a plain integer. This keeps the soundness
  and i32-value classes precise without false value-divergences on refs.
- **Stateful** multi-invoke tests run each invoke on a **fresh instance**, so a
  test whose result depends on state a prior invoke set up (e.g. a populated
  table) is not replicated. The production engines are ground truth; the `.wast`
  assert is only a **fallback** oracle (used when no live engine can run the case)
  and a corroboration column — it never overrides engine consensus. Running each
  module's invoke sequence on one persistent instance is the way to cover these.
- `smith` is random (counts vary run-to-run) and finds shallow over-traps, never
  the subtyping soundness class.
- **Validation-differential** (does the SUT *reject* the spec's `assert_invalid`
  modules?) is not yet implemented — it needs the SUT to expose a validate/load
  step separate from invoke. Those cases are reported as `validation-only not run`.

## Config (env, auto-detected otherwise)

| var          | default                                                          |
|--------------|------------------------------------------------------------------|
| `V8_ORACLE`  | bundled `oracle/v8.js`                                            |
| `NODE`       | newest WasmGC-capable node (≥22) on PATH or in `~/.nvm`          |
| `SPEC_WASM`  | unset — path to the WebAssembly reference interpreter (`spec` oracle) |
| `CUSTOM_CMD` | unset — wire the interpreter under test as the `custom` engine    |

Divergences are also written to `work/divergences.txt`.
