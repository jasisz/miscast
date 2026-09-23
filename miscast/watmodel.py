"""A tiny reference interpreter for the folded, straight-line integer WAT that `intalg` emits.

It reads the module *text* (not the generator's internal state), so it is an oracle independent of how the
module was generated and of every wasmtime backend: a result that all of Cranelift / Winch / Pulley agree on
can still be checked against it. Supported: one memory with active data segments, zero-param exports
returning i64, `local.get` / `local.set`, `select`, and the i32 / i64 const, arithmetic, bitwise, shift,
rotate, compare, bit-count, extend / wrap, div / rem and load instructions. Anything else raises, so a
generator change that outgrows the model fails loudly instead of producing a wrong expectation.
"""

import re

_TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')
_W = {"i32": 32, "i64": 64}


class Unsupported(Exception):
    pass


class Trap(Exception):
    pass


def parse(text):
    stack = [[]]
    for tok in _TOK.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(tok)
    return stack[0]


def _unescape(s):
    body = s[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i] == "\\":
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
        else:
            out += body[i].encode()
            i += 1
    return bytes(out)


def _sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def _div_trunc(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


class _Func:
    def __init__(self, mem, node):
        self.mem = mem
        self.locals = {}
        self.body = []
        for item in node[1:]:
            if isinstance(item, list) and item and item[0] == "local":
                self.locals[item[1]] = 0
            elif isinstance(item, list) and item and item[0] in ("export", "result", "param", "type"):
                if item[0] == "param":
                    raise Unsupported("params")
            elif isinstance(item, list):
                self.body.append(item)
            elif not item.startswith("$"):
                raise Unsupported(f"unfolded instruction {item}")

    def run(self):
        last = None
        for ins in self.body:
            last = self.ev(ins)
        return last

    def load(self, t, op, addr, off):
        ea = addr + off
        size = {"load": _W[t] // 8, "load8_s": 1, "load8_u": 1, "load16_s": 2, "load16_u": 2,
                "load32_s": 4, "load32_u": 4}[op]
        if ea + size > len(self.mem):
            raise Trap("out of bounds")
        v = int.from_bytes(self.mem[ea:ea + size], "little")
        if op.endswith("_s"):
            v = _sx(v, size * 8)
        return v & ((1 << _W[t]) - 1)

    def ev(self, n):
        op = n[0]
        if op == "local.get":
            return self.locals[n[1]]
        if op == "local.set":
            self.locals[n[1]] = self.ev(n[2])
            return None
        if op == "select":
            args = [a for a in n[1:] if not (isinstance(a, list) and a and a[0] == "result")]
            a, b, c = (self.ev(x) for x in args)
            return a if c else b
        t, _, name = op.partition(".")
        if t not in _W:
            raise Unsupported(op)
        w = _W[t]
        m = (1 << w) - 1
        if name == "const":
            return int(n[1], 0) & m
        if name.startswith("load"):
            off = 0
            rest = n[1:]
            while rest and isinstance(rest[0], str):
                if rest[0].startswith("offset="):
                    off = int(rest[0][7:], 0)
                elif not rest[0].startswith("align="):
                    raise Unsupported(f"{op} immediate {rest[0]}")
                rest = rest[1:]
            return self.load(t, name, self.ev(rest[0]), off)
        vals = [self.ev(x) for x in n[1:]]
        if len(vals) == 1:
            (x,) = vals
            if name == "clz":
                return w - x.bit_length()
            if name == "ctz":
                return w if x == 0 else (x & -x).bit_length() - 1
            if name == "popcnt":
                return bin(x).count("1")
            if name == "eqz":
                return int(x == 0)
            if name in ("extend8_s", "extend16_s", "extend32_s"):
                return _sx(x, int(name[6:-2])) & m
            if name == "extend_i32_s":
                return _sx(x, 32) & m
            if name == "extend_i32_u":
                return x & 0xFFFFFFFF
            if name == "wrap_i64":
                return x & 0xFFFFFFFF
            raise Unsupported(op)
        if len(vals) != 2:
            raise Unsupported(f"{op} with {len(vals)} operands")
        a, b = vals
        sa, sb = _sx(a, w), _sx(b, w)
        k = b % w
        if name == "add":
            return (a + b) & m
        if name == "sub":
            return (a - b) & m
        if name == "mul":
            return (a * b) & m
        if name == "and":
            return a & b
        if name == "or":
            return a | b
        if name == "xor":
            return a ^ b
        if name == "shl":
            return (a << k) & m
        if name == "shr_u":
            return a >> k
        if name == "shr_s":
            return (sa >> k) & m
        if name == "rotl":
            return ((a << k) | (a >> (w - k))) & m if k else a
        if name == "rotr":
            return ((a >> k) | (a << (w - k))) & m if k else a
        if name in ("div_u", "rem_u", "div_s", "rem_s"):
            if b == 0:
                raise Trap("integer divide by zero")
            if name == "div_u":
                return a // b
            if name == "rem_u":
                return a % b
            if name == "div_s":
                if sa == -(1 << (w - 1)) and sb == -1:
                    raise Trap("integer overflow")
                return _div_trunc(sa, sb) & m
            return (sa - _div_trunc(sa, sb) * sb) & m
        cmp = {"eq": a == b, "ne": a != b, "lt_u": a < b, "gt_u": a > b, "le_u": a <= b, "ge_u": a >= b,
               "lt_s": sa < sb, "gt_s": sa > sb, "le_s": sa <= sb, "ge_s": sa >= sb}
        if name in cmp:
            return int(cmp[name])
        raise Unsupported(op)


def expected(wat):
    """{export name: signed i64 result} for every zero-param export of `wat`."""
    mod = parse(wat)[0]
    assert mod[0] == "module"
    pages = 0
    segs = []
    funcs = []
    for item in mod[1:]:
        if not isinstance(item, list):
            continue
        if item[0] == "memory":
            nums = [x for x in item[1:] if isinstance(x, str) and x.isdigit()]
            pages = int(nums[0])
        elif item[0] == "data":
            off = item[1]
            if not (isinstance(off, list) and off[0] == "i32.const"):
                raise Unsupported("data offset")
            segs.append((int(off[1], 0), b"".join(_unescape(s) for s in item[2:])))
        elif item[0] == "func":
            funcs.append(item)
        else:
            raise Unsupported(f"module field {item[0]}")
    mem = bytearray(pages * 65536)
    for off, data in segs:
        mem[off:off + len(data)] = data
    out = {}
    for f in funcs:
        exp = [x for x in f[1:] if isinstance(x, list) and x and x[0] == "export"]
        if not exp:
            continue
        name = exp[0][1].strip('"')
        out[name] = _sx(_Func(mem, f).run(), 64)
    return out


def _selftest():
    """Spot checks against hand-computed wasm semantics."""
    def one(expr, t="i64"):
        wrap = expr if t == "i64" else f"(i64.extend_i32_u {expr})"
        return expected(f'(module (memory 1) (func (export "f") (result i64) {wrap}))')["f"]
    assert one("(i32.div_s (i32.const -7) (i32.const 2))", "i32") == 0xFFFFFFFD
    assert one("(i32.rem_s (i32.const -7) (i32.const 2))", "i32") == 0xFFFFFFFF
    assert one("(i32.rem_s (i32.const -2147483648) (i32.const -1))", "i32") == 0
    assert one("(i32.shl (i32.const 1) (i32.const 33))", "i32") == 2
    assert one("(i64.shr_s (i64.const -8) (i64.const 65))") == -4
    assert one("(i32.rotr (i32.const 1) (i32.const 1))", "i32") == 0x80000000
    assert one("(i64.clz (i64.const 0))") == 64
    assert one("(i32.ctz (i32.const 0))", "i32") == 32
    assert one("(i64.extend8_s (i64.const 0x80))") == -128
    assert one("(select (i64.const 1) (i64.const 2) (i32.const 0))") == 2


if __name__ == "__main__":
    _selftest()
    print("ok")
