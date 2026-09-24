"""Random tail-call / call-ABI modules for backend differentials (Cranelift vs Winch vs Pulley).

A family of functions with random signatures (up to ~24 params mixing i32/i64/f32/f64/v128, so many are
passed on the stack; up to 6 results, so some return through a stack area) chain into each other with
`return_call` / `return_call_indirect` (and occasionally plain calls and throws) until a global fuel
counter runs out. Every function folds all of its incoming arguments into a global i64 hash, so a
single argument lost / shuffled / clobbered across a tail call — the stack-argument shuffle that a
callee-pop convention has to get right when caller and callee have different stack-arg sizes — changes
the exported result. No float arithmetic is performed, so every value is bit-exact deterministic.
"""

import random

VT = ["i32", "i64", "f32", "f64", "v128"]


def _mix_param(t, i):
    g = f"(local.get {i})"
    if t == "i32":
        v = f"(i64.extend_i32_u {g})"
    elif t == "i64":
        v = g
    elif t == "f32":
        v = f"(i64.extend_i32_u (i32.reinterpret_f32 {g}))"
    elif t == "f64":
        v = f"(i64.reinterpret_f64 {g})"
    else:
        v = f"(i64.xor (i64x2.extract_lane 0 {g}) (i64.rotl (i64x2.extract_lane 1 {g}) (i64.const 17)))"
    return f"(global.set $h (i64.add (i64.mul (global.get $h) (i64.const 0x100000001b3)) {v}))"


def _val(t, salt, quiet_f32=False):
    """A value of type t derived from the running hash (no float arithmetic). `quiet_f32` sets the f32 quiet bit,
    so no f32 value is a signaling NaN (for engines that quiet f32 sNaNs on every move, e.g. wasm3)."""
    h = f"(i64.xor (global.get $h) (i64.const {salt}))"
    if t == "i32":
        return f"(i32.wrap_i64 (i64.rotr {h} (i64.const {salt % 64})))"
    if t == "i64":
        return f"(i64.rotl {h} (i64.const {salt % 64}))"
    if t == "f32":
        bits = f"(i32.wrap_i64 {h})"
        if quiet_f32:
            bits = f"(i32.or {bits} (i32.const 0x400000))"
        return f"(f32.reinterpret_i32 {bits})"
    if t == "f64":
        return f"(f64.reinterpret_i64 {h})"
    return f"(i64x2.replace_lane 1 (i64x2.splat {h}) (i64.rotl {h} (i64.const 29)))"


def gen_module(seed, n_funcs=None, exn=True, single_result=False, simd=True, quiet_f32=False):
    """`exn=False` drops exception handling and `single_result=True` makes each export return one i64 (the hash
    mixed with the remaining fuel), for engines without EH or harnesses that compare a single value. The random
    stream is consumed identically, so the default modules are unchanged."""
    r = random.Random(seed)
    vt = VT if simd else [t for t in VT if t != "v128"]  # (a different stream only when simd=False)
    n_res_classes = r.randrange(1, 4)
    res_classes = []
    for _ in range(n_res_classes):
        k = r.choice([0, 1, 1, 2, 3, 5, 6])
        res_classes.append([r.choice(vt) for _ in range(k)])
    n_funcs = n_funcs or r.randrange(4, 12)
    funcs = []  # (params, rc)
    for _ in range(n_funcs):
        np = r.choice([0, 1, 2, 3, 5, 8, 9, 12, 16, 20, 24])
        params = [r.choice(vt) for _ in range(np)]
        funcs.append((params, r.randrange(n_res_classes)))
    use_exn = r.random() < 0.4 and exn
    types = []
    for fi, (params, rc) in enumerate(funcs):
        p = " ".join(params)
        res = " ".join(res_classes[rc])
        types.append(f"  (type $t{fi} (func (param {p}) (result {res})))")
    lines = ["(module"] + types
    lines.append("  (global $h (mut i64) (i64.const 0))")
    lines.append("  (global $fuel (mut i32) (i32.const 0))")
    lines.append(f"  (table $tab {n_funcs} funcref)")
    lines.append(f"  (elem (table $tab) (i32.const 0) func {' '.join(f'$f{i}' for i in range(n_funcs))})")
    if use_exn:
        lines.append("  (tag $e (param i64 i32))")

    def args_for(ti, salt0):
        return " ".join(_val(t, salt0 * 131 + j * 7 + 1, quiet_f32) for j, t in enumerate(funcs[ti][0]))

    for fi, (params, rc) in enumerate(funcs):
        res = res_classes[rc]
        body = [_mix_param(t, i) for i, t in enumerate(params)]
        body.append(f"(global.set $h (i64.add (global.get $h) (i64.const {fi * 1000 + 7})))")
        # base case
        ret_vals = " ".join(_val(t, fi * 97 + j, quiet_f32) for j, t in enumerate(res))
        body.append(f"(if (i32.eqz (global.get $fuel)) (then (return {ret_vals})))")
        body.append("(global.set $fuel (i32.sub (global.get $fuel) (i32.const 1)))")
        # optional interior non-tail call into another class, fold its results
        if r.random() < 0.35:
            tj = r.randrange(n_funcs)
            nres = len(res_classes[funcs[tj][1]])
            call = f"(call $f{tj} {args_for(tj, fi + 50)})"
            if use_exn and r.random() < 0.5:
                # catch a throw somewhere down the chain
                blk = f"(block $c (result i64 i32) (try_table (catch $e $c) {call} {' '.join(['(drop)'] * nres)}) (i64.const 0) (i32.const 0))"
                body.append(f"{blk} (drop) (global.set $h (i64.xor (global.get $h)))")
            else:
                body.append(call)
                for k, t in reversed(list(enumerate(res_classes[funcs[tj][1]]))):
                    lt = f"$tmp{t}"
                    body.append(f"(local.set {lt})")
                    body.append(_mix_param_local(t, lt))
        if use_exn and r.random() < 0.12:
            body.append(f"(if (i32.eq (i32.and (global.get $fuel) (i32.const 3)) (i32.const 1)) "
                        f"(then (throw $e (global.get $h) (global.get $fuel))))")
        # tail call to a function with the same result class
        same = [j for j, (_, rcj) in enumerate(funcs) if rcj == rc]
        tj = r.choice(same)
        kind = r.random()
        if kind < 0.55:
            body.append(f"(return_call $f{tj} {args_for(tj, fi)})")
        elif kind < 0.85:
            body.append(f"(return_call_indirect $tab (type $t{tj}) {args_for(tj, fi)} (i32.const {tj}))")
        else:
            body.append(f"(call $f{tj} {args_for(tj, fi)})")
        locs = " ".join(f"(local $tmp{t} {t})" for t in vt)
        lines.append(f"  (func $f{fi} (type $t{fi}) {locs}\n    " + "\n    ".join(body) + ")")
    # entry points
    for ei in range(min(n_funcs, 6)):
        fi = r.randrange(n_funcs)
        fuel = r.choice([0, 1, 2, 5, 17, 64, 300])
        res = res_classes[funcs[fi][1]]
        body = [f"(global.set $h (i64.const {r.getrandbits(63)}))", f"(global.set $fuel (i32.const {fuel}))",
                f"(call $f{fi} {args_for(fi, 999 + ei)})"]
        for t in reversed(res):
            body.append(f"(local.set $tmp{t})")
            body.append(_mix_param_local(t, f"$tmp{t}"))
        wrapped = body
        if use_exn:
            inner = " ".join(wrapped)
            wrapped = [f"(block $c (result i64 i32) (try_table (catch $e $c) {inner}) (i64.const 0) (i32.const 0))",
                       "(global.set $fuel)", "(global.set $h (i64.xor (global.get $h)))"]
        locs = " ".join(f"(local $tmp{t} {t})" for t in vt)
        if single_result:
            lines.append(f"  (func (export \"e{ei}\") (result i64) {locs}\n    " + "\n    ".join(wrapped)
                         + "\n    (i64.xor (global.get $h) (i64.extend_i32_u (global.get $fuel))))")
        else:
            lines.append(f"  (func (export \"e{ei}\") (result i64 i32) {locs}\n    " + "\n    ".join(wrapped)
                         + "\n    (global.get $h) (global.get $fuel))")
    return "\n".join(lines) + ")\n"


def _mix_param_local(t, name):
    return _mix_param(t, name)


def gen_module_i64(seed):
    """No exceptions, one i64 result per export: runs on engines without EH, checkable by a single-value oracle."""
    return gen_module(seed, exn=False, single_result=True)


def gen_module_portable(seed):
    """Like gen_module_i64 but without v128 values, for engines built without SIMD (e.g. WAMR, wasmer singlepass)."""
    return gen_module(seed, exn=False, single_result=True, simd=False)


def gen_module_portable_qnan(seed):
    """gen_module_portable with every generated f32 forced to a non-signaling bit pattern."""
    return gen_module(seed, exn=False, single_result=True, simd=False, quiet_f32=True)
