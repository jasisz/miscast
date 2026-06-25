"""Exception-handling (try_table / throw / throw_ref / exnref) self-checking differential.

The standardized exception-handling model — `try_table` with `catch` / `catch_ref` / `catch_all` /
`catch_all_ref`, `throw`, `throw_ref`, and `exnref` as a first-class reference value — is recent enough
that the maturing engines are still settling it, yet old enough that the production engines agree. Each
program here is SELF-CHECKING: it exercises one corner of the surface and returns a sentinel that, by
construction, only the conformant unwinding/forwarding produces (or it traps where the spec mandates a
trap). So the per-program oracle is baked in — an engine that mis-forwards a tag param, drops an exnref,
canonicalizes the wrong handler, or fails to trap on a null `throw_ref` diverges with no second engine
required.

The surface covered: plain/multi-param `catch`; `catch_all` discarding params; `catch_ref` /
`catch_all_ref` capturing an `exnref`; `throw_ref` re-raising (payload preserved); propagation past a
non-matching handler; the no-throw fall-through; `exnref` stored in a GC struct field / array element /
passed across a call frame; a tag carrying a GC reference forwarded across a deep unwind; multi-value
tags; branching out of a handler; and the two mandated null-`exnref` `throw_ref` traps. The spec-invalid
EH modules (catch/throw arity + type rules) live in `corpus/invalid/` and run through the validation
differential, not here.

Every module validates under wasm-tools and agrees across wasmtime (-W exceptions=y), WasmEdge and V8;
the `__main__` harness below re-confirms that agreement.
"""

# (label, export, expected, wat) — expected is "OK <sentinel>" for a value self-check, or "TRAP".
_PROGRAMS = [
    ("catch-single-param", "main", "OK 42", '''(module
  (tag $e (param i32))
  (func (export "main") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 41)
        (throw $e)
        (unreachable))
      (return))
    (i32.add (i32.const 1))))'''),

    ("catch-multi-param", "main", "OK 703", '''(module
  (tag $e (param i32 i32))
  (func (export "main") (result i32) (local $a i32) (local $b i32)
    (block $h (result i32 i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 7)
        (i32.const 3)
        (throw $e)
        (unreachable))
      (return))
    (local.set $b)
    (local.set $a)
    (i32.add (i32.mul (local.get $a) (i32.const 100)) (local.get $b))))'''),

    ("catch-all-discards-params", "main", "OK 777", '''(module
  (tag $e (param i32))
  (func (export "main") (result i32)
    (block $h (result)
      (try_table (result i32) (catch_all $h)
        (i32.const 12345)
        (throw $e)
        (unreachable))
      (return))
    (i32.const 777)))'''),

    ("throw-past-nonmatching-catch", "main", "OK 3022", '''(module
  (tag $e1 (param i32))
  (tag $e2 (param i32))
  (func (export "main") (result i32)
    (block $outer (result i32)
      (try_table (result i32) (catch $e2 $outer)
        (block $inner (result i32)
          (try_table (result i32) (catch $e1 $inner)
            (i32.const 22)
            (throw $e2)
            (unreachable))
          (i32.add (i32.const 1))
          (return))
        (return))
      (return))
    (i32.add (i32.const 3000))))'''),

    ("no-throw-fall-through", "main", "OK 506", '''(module
  (tag $e (param i32))
  (func (export "main") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 500)
        (i32.add (i32.const 6)))
      (return))
    (i32.add (i32.const 1))))'''),

    ("catch-ref-rethrow-to-outer", "main", "OK 1055", '''(module
  (tag $e (param i32))
  (func (export "main") (result i32) (local $ex exnref)
    (block $outer (result i32)
      (try_table (result i32) (catch $e $outer)
        (block $inner (result i32 exnref)
          (try_table (result i32) (catch_ref $e $inner)
            (i32.const 55)
            (throw $e)
            (unreachable))
          (return))
        (local.set $ex)
        (drop)
        (local.get $ex)
        (throw_ref))
      (return))
    (i32.add (i32.const 1000))))'''),

    ("catch-all-ref-rethrow-to-outer", "main", "OK 888", '''(module
  (tag $e (param i32 i64))
  (func (export "main") (result i32) (local $ex exnref)
    (block $outer (result)
      (try_table (result i32) (catch_all $outer)
        (block $inner (result exnref)
          (try_table (result i32) (catch_all_ref $inner)
            (i32.const 9)
            (i64.const 99)
            (throw $e)
            (unreachable))
          (return))
        (throw_ref))
      (return))
    (i32.const 888)))'''),

    ("exnref-in-gc-struct-field", "main", "OK 2064", '''(module
  (type $box (struct (field $ex (mut exnref))))
  (tag $e (param i32))
  (func (export "main") (result i32) (local $b (ref $box)) (local $ex exnref)
    (local.set $b (struct.new $box (ref.null exn)))
    (block $outer (result i32)
      (try_table (result i32) (catch $e $outer)
        (block $inner (result i32 exnref)
          (try_table (result i32) (catch_ref $e $inner)
            (i32.const 64)
            (throw $e)
            (unreachable))
          (return))
        (local.set $ex)
        (drop)
        (struct.set $box $ex (local.get $b) (local.get $ex))
        (throw_ref (struct.get $box $ex (local.get $b))))
      (return))
    (i32.add (i32.const 2000))))'''),

    ("exnref-in-array-elem-rethrow", "main", "OK 33", '''(module
  (type $arr (array (mut exnref)))
  (tag $e (param i32))
  (func (export "main") (result i32)
    (local $a (ref $arr))
    (local $cap exnref)
    (block $inner (result i32 exnref)
      (try_table (catch_ref $e $inner)
        (i32.const 33)
        (throw $e))
      (unreachable))
    (local.set $cap)
    (drop)
    (local.set $a (array.new $arr (ref.null exn) (i32.const 3)))
    (array.set $arr (local.get $a) (i32.const 1) (local.get $cap))
    (block $outer
      (try_table (catch_all $outer)
        (array.get $arr (local.get $a) (i32.const 1))
        (throw_ref))
      (unreachable))
    (i32.const 33)))'''),

    ("exnref-through-call-frame", "main", "OK 44", '''(module
  (tag $e (param i32))
  (func $reraise (param $x exnref)
    (local.get $x)
    (throw_ref))
  (func (export "main") (result i32)
    (local $cap exnref)
    (block $inner (result i32 exnref)
      (try_table (catch_ref $e $inner)
        (i32.const 44)
        (throw $e))
      (unreachable))
    (local.set $cap)
    (drop)
    (block $outer
      (try_table (catch_all $outer)
        (call $reraise (local.get $cap)))
      (unreachable))
    (i32.const 44)))'''),

    ("throw-ref-payload-survives-reraise", "main", "OK 120", '''(module
  (tag $e (param i32))
  (func (export "main") (result i32)
    (local $cap exnref)
    (local $sum i32)
    (block $inner (result i32 exnref)
      (try_table (catch_ref $e $inner)
        (i32.const 60)
        (throw $e))
      (unreachable))
    (local.set $cap)
    (local.set $sum)
    (block $outer (result i32)
      (try_table (result i32) (catch $e $outer)
        (local.get $cap)
        (throw_ref))
      (unreachable))
    (local.get $sum)
    (i32.add)))'''),

    ("catch-all-ref-capture-rethrow", "main", "OK 77", '''(module
  (tag $e (param i32))
  (func (export "main") (result i32)
    (local $cap exnref)
    (block $inner (result exnref)
      (try_table (catch_all_ref $inner)
        (i32.const 0)
        (throw $e))
      (unreachable))
    (local.set $cap)
    (block $outer
      (try_table (catch_all $outer)
        (local.get $cap)
        (throw_ref))
      (unreachable))
    (i32.const 77)))'''),

    ("result-throw-taken", "run", "OK 43", '''(module
  (tag $e (param i32))
  (func (export "run") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 42)
        (throw $e)
        (i32.const 999))
      (return))
    (i32.const 1)
    (i32.add)))'''),

    ("branch-out-of-handler", "run", "OK 70", '''(module
  (tag $e (param i32))
  (func (export "run") (result i32)
    (block $out (result i32)
      (block $h (result i32)
        (try_table (result i32) (catch $e $h)
          (i32.const 7)
          (throw $e))
        (br $out))
      (br $out))
    (i32.const 10)
    (i32.mul)))'''),

    ("gc-struct-ref-across-unwind", "run", "OK 31", '''(module
  (type $pt (struct (field $x i32) (field $y i32)))
  (tag $e (param (ref $pt)))
  (func (export "run") (result i32)
    (block $h (result (ref $pt))
      (try_table (result i32) (catch $e $h)
        (struct.new $pt (i32.const 11) (i32.const 31))
        (throw $e)
        (unreachable))
      (return))
    (struct.get $pt $y)))'''),

    ("deep-unwind-two-frames", "run", "OK 105", '''(module
  (tag $e (param i32))
  (func $deepest (param i32) (result i32)
    (local.get 0)
    (throw $e))
  (func $mid (param i32) (result i32)
    (local.get 0)
    (call $deepest))
  (func (export "run") (result i32)
    (block $h (result i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 5)
        (call $mid))
      (return))
    (i32.const 100)
    (i32.add)))'''),

    ("multi-value-tag", "run", "OK 33", '''(module
  (tag $e (param i32 i64 i32))
  (func (export "run") (result i32)
    (local $a i32)
    (local $b i64)
    (local $c i32)
    (block $h (result i32 i64 i32)
      (try_table (result i32) (catch $e $h)
        (i32.const 10)
        (i64.const 20)
        (i32.const 3)
        (throw $e))
      (return))
    (local.set $c)
    (local.set $b)
    (local.set $a)
    (local.get $a)
    (local.get $b) (i32.wrap_i64)
    (i32.add)
    (local.get $c)
    (i32.add)))'''),

    ("gc-array-ref-deep-unwind", "run", "OK 15", '''(module
  (type $arr (array (mut i32)))
  (tag $e (param (ref $arr)))
  (func $make (result (ref $arr))
    (array.new_fixed $arr 3
      (i32.const 4) (i32.const 8) (i32.const 15)))
  (func $thrower (param (ref $arr)) (result i32)
    (local.get 0)
    (throw $e))
  (func $passthru (param (ref $arr)) (result i32)
    (local.get 0)
    (call $thrower))
  (func (export "run") (result i32)
    (block $h (result (ref $arr))
      (try_table (result i32) (catch $e $h)
        (call $make)
        (call $passthru))
      (return))
    (i32.const 2)
    (array.get $arr)))'''),

    ("throw-ref-null-traps", "main", "TRAP", '''(module
  (func (export "main") (result i32)
    (ref.null exn)
    (throw_ref)
    (i32.const 55)))'''),

    ("throw-ref-null-array-elem-traps", "main", "TRAP", '''(module
  (type $arr (array (mut exnref)))
  (tag $e (param i32))
  (func (export "main") (result i32)
    (local $a (ref $arr))
    (local.set $a (array.new $arr (ref.null exn) (i32.const 2)))
    (block $outer
      (try_table (catch_all $outer)
        (array.get $arr (local.get $a) (i32.const 0))
        (throw_ref))
      (unreachable))
    (i32.const 88)))'''),
]


def count():
    return len(_PROGRAMS)


def gen(seed):
    """Return (label, export, expected, wat) for the seed-th program (cycling)."""
    return _PROGRAMS[seed % len(_PROGRAMS)]


if __name__ == "__main__":
    import subprocess, re, os
    # engine binaries live in a STABLE dir (~/wasm-engines), not /tmp — `source ~/wasm-engines/ENGINES.env`
    # exports these; the defaults here match that layout.
    ENG = os.environ.get("WASM_ENGINES", os.path.expanduser("~/wasm-engines"))
    os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("WASMEDGE_LIB", f"{ENG}/wasmedge/lib")
    WT = os.environ.get("WASMTIME_BIN", f"{ENG}/wasmtime-v46/wasmtime")
    WE = os.environ.get("WASMEDGE_BIN", f"{ENG}/wasmedge/bin/wasmedge")
    MCR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runner", "target", "release", "mc-runner")
    NODE = os.environ.get("NODE26", os.path.expanduser("~/.nvm/versions/node/v26.3.0/bin/node"))
    V8 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oracle", "v8.js")

    def verdict(p):
        both = ((p.stdout or "") + (p.stderr or "")).lower()
        if "trap" in both or "unreachable" in both or "null" in both or "exception" in both:
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "?:" + (both.strip().splitlines()[-1][:30] if both.strip() else "")

    def run(eng, wat, export):
        open("/tmp/eh.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/eh.wat", "-o", "/tmp/eh.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            return "ASMFAIL:" + a.stderr.strip().splitlines()[-1][:44]
        if eng == "wt":
            p = subprocess.run([WT, "run", "-W", "function-references=y,gc=y,exceptions=y", "--invoke", export, "/tmp/eh.wasm"], capture_output=True, text=True)
        elif eng == "we":
            p = subprocess.run([WE, "run", "/tmp/eh.wasm", export], capture_output=True, text=True)
        elif eng == "mcr":
            p = subprocess.run([MCR, "/tmp/eh.wasm", "--invoke", export], capture_output=True, text=True)
        elif eng == "v8":
            p = subprocess.run([NODE, V8, "/tmp/eh.wasm", export], capture_output=True, text=True)
        return verdict(p)

    print("=== exception-handling self-check: conformant engines must agree with the baked oracle ===")
    bad = 0
    for label, export, expected, wat in _PROGRAMS:
        row = {e: run(e, wat, export) for e in ["wt", "we", "mcr", "v8"]}
        ok = all(v == expected for v in row.values())
        if not ok:
            bad += 1
        print(f"  {'OK ' if ok else 'FAIL'} {label:34} exp={expected:8} {row}")
    print(f"\n{len(_PROGRAMS)} programs, {bad} disagreeing")
