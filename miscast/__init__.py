"""miscast — a .wast-native differential soundness tester for wasm interpreters.

Runs the invocations described by WebAssembly .wast scripts through several
engines and flags divergences — above all SOUNDNESS divergences, where the
system-under-test runs something the trusted oracles trap on.

Native input is .wast (the official wasm script format): a module plus how to
exercise it — (assert_return (invoke "fn" args) result) / (assert_trap ...). The
assert is the spec author's own expected result, so it acts as a fallback oracle
(conformance). A bare .wat is accepted too — exercised by (invoke "f").

Run it with:  python3 -m miscast --sut ENGINE [--mode replay|mutate|smith] ...

No third-party dependencies — only the standard library plus the external CLI
tools wasm-tools, node (>=22, for the V8 oracle) and wasmtime.

Package layout:
  config     paths + engine-binary discovery
  toolchain  cached wasm-tools prepare (assemble + validate) + u32
  engines    per-engine backends + detection
  verdict    classify the SUT against the agreeing oracle pool
  runner     run one case through every engine and classify
  mutate     re-point a type slot in a module (the subtyping sweep)
  wast       .wast parsing -> runnable (module, invoke, expected) cases
  modes      replay / mutate / smith generation
  cli        argument parsing + the run loop
"""
__version__ = "0.1.0"
