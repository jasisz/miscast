"""Stateful GC heap-storm generator.

The focused alias modes test one route at a time. This generator builds longer programs where several
storage locations keep aliases to a small GC heap, then interleaves mutations, bulk copies, table/global
stores, casts, exception routing, extern round-trips, and call_ref/tail-call mutation. A Python shadow model
computes the final weighted checksum; a conformant engine must produce the same value.
"""

_NOBJ = 6
_NSLOTS = 8


class _Rng:
    def __init__(self, seed):
        self.state = (0x9E3779B9 ^ (seed * 0x45D9F3B)) & 0xFFFFFFFF

    def pick(self, n):
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        return self.state % n


_SCENARIOS = [
    ("bulk-core", {"extern": False, "eh": False, "throw_ref": False, "tail": False}),
    ("control-catch", {"extern": False, "eh": True, "throw_ref": False, "tail": False}),
    ("extern-bulk", {"extern": True, "eh": False, "throw_ref": False, "tail": False}),
    ("tail-bulk", {"extern": False, "eh": False, "throw_ref": False, "tail": True}),
    ("eh-reraise", {"extern": False, "eh": True, "throw_ref": True, "tail": False}),
    ("full-mix", {"extern": True, "eh": True, "throw_ref": True, "tail": True}),
]

_TYPES = """  (type $cell (sub (struct (field (mut i32)))))
  (type $box (sub (struct (field (mut (ref $cell))) (field (mut (ref $cell))))))
  (type $arr (array (mut (ref null $cell))))
  (type $mut (sub (func (param (ref $cell)) (param i32) (result (ref $cell)))))
"""


def _cell_ref(i):
    return f"(local.get $c{i})"


def _arr_ref(slot):
    return f"(ref.as_non_null (array.get $arr (local.get $a) (i32.const {slot})))"


def _table_ref(slot):
    return f"(ref.as_non_null (table.get $t (i32.const {slot})))"


def _box_ref(field):
    return f"(struct.get $box {field} (local.get $b))"


def _global_ref():
    return "(ref.as_non_null (global.get $g))"


def _bump(ref_expr, delta):
    return f"(drop (call_ref $mut {ref_expr} (i32.const {delta}) (ref.func $bump)))"


def _weighted_sum(terms):
    expr = "(i32.const 0)"
    for weight, ref_expr in terms:
        expr = f"""(i32.add
      {expr}
      (i32.mul (struct.get $cell 0 {ref_expr}) (i32.const {weight})))"""
    return expr


def _make(seed, scenario, flags):
    rng = _Rng(seed)
    values = [100 + seed * 13 + i * 17 for i in range(_NOBJ)]
    arr = [0, 1, 2, 3, 4, 5, 0, 1]
    table = [2, 3, 4, 5, 0, 1, 2, 3]
    box = [0, 1]
    glob = 3
    steps = []

    def delta():
        return 1 + rng.pick(37)

    def copy_slots(slots, dst, src, n):
        tmp = slots[src:src + n]
        for i, v in enumerate(tmp):
            slots[dst + i] = v

    def emit_mut_local():
        i, d = rng.pick(_NOBJ), delta()
        values[i] += d
        steps.append(_bump(_cell_ref(i), d))

    def emit_mut_array():
        slot, d = rng.pick(_NSLOTS), delta()
        values[arr[slot]] += d
        steps.append(_bump(_arr_ref(slot), d))

    def emit_mut_table():
        slot, d = rng.pick(_NSLOTS), delta()
        values[table[slot]] += d
        steps.append(_bump(_table_ref(slot), d))

    def emit_mut_box():
        field, d = rng.pick(2), delta()
        values[box[field]] += d
        steps.append(_bump(_box_ref(field), d))

    def emit_mut_global():
        nonlocal glob
        d = delta()
        values[glob] += d
        steps.append(_bump(_global_ref(), d))

    def emit_array_set():
        slot, src = rng.pick(_NSLOTS), rng.pick(_NOBJ)
        arr[slot] = src
        steps.append(f"(array.set $arr (local.get $a) (i32.const {slot}) {_cell_ref(src)})")

    def emit_array_copy():
        n = 1 + rng.pick(3)
        dst = rng.pick(_NSLOTS - n + 1)
        src = rng.pick(_NSLOTS - n + 1)
        copy_slots(arr, dst, src, n)
        steps.append(
            f"(array.copy $arr $arr (local.get $a) (i32.const {dst}) "
            f"(local.get $a) (i32.const {src}) (i32.const {n}))"
        )

    def emit_array_fill():
        n = 1 + rng.pick(3)
        dst = rng.pick(_NSLOTS - n + 1)
        src = rng.pick(_NOBJ)
        for i in range(n):
            arr[dst + i] = src
        steps.append(f"(array.fill $arr (local.get $a) (i32.const {dst}) {_cell_ref(src)} (i32.const {n}))")

    def emit_table_set():
        slot, src = rng.pick(_NSLOTS), rng.pick(_NOBJ)
        table[slot] = src
        steps.append(f"(table.set $t (i32.const {slot}) {_cell_ref(src)})")

    def emit_table_copy():
        n = 1 + rng.pick(3)
        dst = rng.pick(_NSLOTS - n + 1)
        src = rng.pick(_NSLOTS - n + 1)
        copy_slots(table, dst, src, n)
        steps.append(f"(table.copy $t $t (i32.const {dst}) (i32.const {src}) (i32.const {n}))")

    def emit_table_fill():
        n = 1 + rng.pick(3)
        dst = rng.pick(_NSLOTS - n + 1)
        src = rng.pick(_NOBJ)
        for i in range(n):
            table[dst + i] = src
        steps.append(f"(table.fill $t (i32.const {dst}) {_cell_ref(src)} (i32.const {n}))")

    def emit_box_set():
        field, src = rng.pick(2), rng.pick(_NOBJ)
        box[field] = src
        steps.append(f"(struct.set $box {field} (local.get $b) {_cell_ref(src)})")

    def emit_global_set():
        nonlocal glob
        glob = rng.pick(_NOBJ)
        steps.append(f"(global.set $g {_cell_ref(glob)})")

    def emit_br_on_cast():
        src, d = rng.pick(_NOBJ), delta()
        values[src] += d
        steps.append(f"""(local.set $tmp
  (block $ok (result (ref $cell))
    (br_on_cast $ok (ref eq) (ref $cell) {_cell_ref(src)})
    (unreachable)))
{_bump("(local.get $tmp)", d)}""")

    def emit_extern():
        src, d = rng.pick(_NOBJ), delta()
        values[src] += d
        steps.append(f"""(local.set $tmp
  (ref.cast (ref $cell)
    (any.convert_extern
      (extern.convert_any {_cell_ref(src)}))))
{_bump("(local.get $tmp)", d)}""")

    def emit_try_table():
        src, d = rng.pick(_NOBJ), delta()
        values[src] += d
        steps.append(f"""(local.set $tmp
  (block $h (result (ref $cell))
    (try_table (catch $e $h)
      (throw $e {_cell_ref(src)}))
    (unreachable)))
{_bump("(local.get $tmp)", d)}""")

    def emit_throw_ref():
        src, d = rng.pick(_NOBJ), delta()
        values[src] += d
        steps.append(f"""(block $cap (result (ref $cell) exnref)
  (try_table (catch_ref $e $cap)
    (throw $e {_cell_ref(src)}))
  (unreachable))
(local.set $ex)
(local.set $tmp)
(local.set $tmp
  (block $h (result (ref $cell))
    (try_table (catch $e $h)
      (throw_ref (local.get $ex))
      (unreachable))
    (unreachable)))
{_bump("(local.get $tmp)", d)}""")

    def emit_return_call_ref():
        src, d = rng.pick(_NOBJ), delta()
        values[src] += d
        steps.append(f"(drop (call $bounce {_cell_ref(src)} (i32.const {d}) (ref.func $bump)))")

    mandatory = [
        emit_array_copy, emit_array_fill, emit_table_copy, emit_table_fill,
        emit_box_set, emit_mut_box, emit_global_set, emit_mut_global, emit_br_on_cast,
    ]
    if flags["extern"]:
        mandatory.append(emit_extern)
    if flags["eh"]:
        mandatory.append(emit_try_table)
    if flags["throw_ref"]:
        mandatory.append(emit_throw_ref)
    if flags["tail"]:
        mandatory.append(emit_return_call_ref)

    for emit in mandatory:
        emit()

    pool = [
        emit_mut_local, emit_mut_array, emit_mut_table, emit_mut_box, emit_mut_global,
        emit_array_set, emit_array_copy, emit_array_fill, emit_table_set, emit_table_copy,
        emit_table_fill, emit_box_set, emit_global_set, emit_br_on_cast,
    ]
    if flags["extern"]:
        pool.append(emit_extern)
    if flags["eh"]:
        pool.append(emit_try_table)
    if flags["throw_ref"]:
        pool.append(emit_throw_ref)
    if flags["tail"]:
        pool.append(emit_return_call_ref)

    for _ in range(18 + (seed % 7) * 3):
        pool[rng.pick(len(pool))]()

    checksum_terms = [
        (3, _cell_ref(0)), (5, _cell_ref(1)), (7, _cell_ref(2)), (11, _cell_ref(3)),
        (13, _cell_ref(4)), (17, _cell_ref(5)), (19, _arr_ref(0)), (23, _arr_ref(3)),
        (29, _arr_ref(6)), (31, _table_ref(1)), (37, _table_ref(4)), (41, _table_ref(7)),
        (43, _box_ref(0)), (47, _box_ref(1)), (53, _global_ref()),
    ]
    expected = (
        3 * values[0] + 5 * values[1] + 7 * values[2] + 11 * values[3] +
        13 * values[4] + 17 * values[5] + 19 * values[arr[0]] + 23 * values[arr[3]] +
        29 * values[arr[6]] + 31 * values[table[1]] + 37 * values[table[4]] +
        41 * values[table[7]] + 43 * values[box[0]] + 47 * values[box[1]] +
        53 * values[glob]
    )

    locals_decl = "\n".join(f"    (local $c{i} (ref $cell))" for i in range(_NOBJ))
    init_cells = "\n".join(
        f"    (local.set $c{i} (struct.new $cell (i32.const {values0})))"
        for i, values0 in enumerate([100 + seed * 13 + j * 17 for j in range(_NOBJ)])
    )
    init_arr = "\n".join(
        f"    (array.set $arr (local.get $a) (i32.const {i}) {_cell_ref(src)})"
        for i, src in enumerate([0, 1, 2, 3, 4, 5, 0, 1])
    )
    init_table = "\n".join(
        f"    (table.set $t (i32.const {i}) {_cell_ref(src)})"
        for i, src in enumerate([2, 3, 4, 5, 0, 1, 2, 3])
    )
    body_steps = "\n".join("    " + line.replace("\n", "\n    ") for line in steps)
    tag_decl = "  (tag $e (param (ref $cell)))\n" if flags["eh"] else ""
    bounce_decl = """  (func $bounce (param (ref $cell)) (param i32) (param (ref $mut)) (result (ref $cell))
    (return_call_ref $mut (local.get 0) (local.get 1) (local.get 2)))
""" if flags["tail"] else ""
    ex_local = "    (local $ex exnref)\n" if flags["throw_ref"] else ""
    wat = f"""(module
{_TYPES}
{tag_decl.rstrip()}
  (global $g (mut (ref null $cell)) (ref.null $cell))
  (table $t {_NSLOTS} (ref null $cell))
  (func $bump (type $mut)
    (struct.set $cell 0 (local.get 0)
      (i32.add (struct.get $cell 0 (local.get 0)) (local.get 1)))
    (local.get 0))
{bounce_decl.rstrip()}
  (elem declare func $bump)
  (func (export "f") (result i32)
{locals_decl}
    (local $a (ref $arr))
    (local $b (ref $box))
    (local $tmp (ref $cell))
{ex_local.rstrip()}
{init_cells}
    (local.set $a (array.new_default $arr (i32.const {_NSLOTS})))
{init_arr}
    (local.set $b (struct.new $box (local.get $c0) (local.get $c1)))
{init_table}
    (global.set $g (local.get $c3))
{body_steps}
    {_weighted_sum(checksum_terms)}))"""
    return f"{scenario}-{len(steps)}ops", f"OK {expected}", wat


def heapstorm_gen(seed):
    """Return (label, export, expected, wat): the seed-th stateful heap-storm probe."""
    scenario, flags = _SCENARIOS[seed % len(_SCENARIOS)]
    label, expected, wat = _make(seed, scenario, flags)
    return f"heapstorm-{label}", "f", expected, wat


_FAMILIES = _SCENARIOS


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in ("trap", "unreachable", "null", "runtimeerror")):
            return "TRAP"
        nums = re.findall(r"-?\d+", p.stdout or "")
        return "OK " + nums[-1] if nums else "OK _"

    print("=== heapstorm: stateful GC alias/storage/call/EH programs with a shadow checksum ===")
    bad = 0
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = heapstorm_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/hs.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:120]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,exceptions=y,tail-call=y",
                                 "--invoke", export, "/tmp/hs.wasm"], capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:32} exp={expected:9} wasmtime={got}")
        else:
            print(f"  OK  {label:32} assembles")
    print(f"\n{2 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
