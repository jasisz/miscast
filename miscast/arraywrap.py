"""GC-array extent arithmetic probes.

All array bulk operations use unsigned i32 offsets and lengths.  Their bounds checks must compute the full
mathematical extent; wrapping ``offset + length`` (or ``source + length * element_size`` for data segments)
can turn a required trap into an out-of-bounds GC-heap access.  Each family includes wrap, ordinary OOB,
exact-end, and zero-length controls with a spec-fixed result.
"""

_U32 = lambda n: f"0x{n:X}" if n >= 0x80000000 else str(n)


def _fill(v):
    dst, length, expected = [
        (0xFFFFFFFE, 4, "TRAP"),
        (0xFFFFFFFF, 1, "TRAP"),
        (3, 2, "TRAP"),
        (2, 2, "OK 77"),
        (4, 0, "OK 13"),
        (0xFFFFFFFF, 0, "TRAP"),
        (5, 0, "TRAP"),
    ][v % 7]
    wat = f"""(module (type $a (array (mut i32)))
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 4
      (i32.const 10) (i32.const 11) (i32.const 12) (i32.const 13)))
    (array.fill $a (local.get $a) (i32.const {_U32(dst)}) (i32.const 77) (i32.const {_U32(length)}))
    (array.get $a (local.get $a) (i32.const 3))))"""
    return f"fill[dst={_U32(dst)},len={_U32(length)}]", expected, wat


def _copy(v):
    dst, src, length, expected, read = [
        (0xFFFFFFFE, 0, 4, "TRAP", 0),
        (0, 0xFFFFFFFE, 4, "TRAP", 0),
        (0xFFFFFFFF, 0xFFFFFFFF, 2, "TRAP", 0),
        (3, 0, 2, "TRAP", 0),
        (2, 0, 2, "OK 22", 3),
        (4, 4, 0, "OK 20", 1),
        (0xFFFFFFFF, 0xFFFFFFFF, 0, "TRAP", 0),
    ][v % 7]
    wat = f"""(module (type $a (array (mut i32)))
  (func (export "f") (result i32) (local $s (ref $a)) (local $d (ref $a))
    (local.set $s (array.new_fixed $a 4
      (i32.const 21) (i32.const 22) (i32.const 23) (i32.const 24)))
    (local.set $d (array.new_fixed $a 4
      (i32.const 10) (i32.const 20) (i32.const 30) (i32.const 40)))
    (array.copy $a $a (local.get $d) (i32.const {_U32(dst)})
      (local.get $s) (i32.const {_U32(src)}) (i32.const {_U32(length)}))
    (array.get $a (local.get $d) (i32.const {read}))))"""
    return f"copy[dst={_U32(dst)},src={_U32(src)},len={_U32(length)}]", expected, wat


def _init_data(v):
    packed, dst, src, length, expected, read = [
        (False, 0xFFFFFFFE, 0, 4, "TRAP", 0),
        (False, 0, 0xFFFFFFFE, 4, "TRAP", 0),
        (True, 0, 0xFFFFFFFC, 1, "TRAP", 0),
        (False, 3, 0, 2, "TRAP", 0),
        (False, 2, 2, 2, "OK 6", 3),
        (False, 4, 8, 0, "OK 9", 0),
        (False, 5, 0, 0, "TRAP", 0),
    ][v % 7]
    ty = "i32" if packed else "i8"
    get = "array.get" if packed else "array.get_u"
    data = "\\01\\00\\00\\00\\02\\00\\00\\00\\03\\00\\00\\00\\04\\00\\00\\00" if packed else "\\03\\04\\05\\06\\07\\08\\09\\0a"
    wat = f"""(module (type $a (array (mut {ty}))) (data $d "{data}")
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new $a (i32.const 9) (i32.const 4)))
    (array.init_data $a $d (local.get $a) (i32.const {_U32(dst)})
      (i32.const {_U32(src)}) (i32.const {_U32(length)}))
    ({get} $a (local.get $a) (i32.const {read}))))"""
    return f"init-data-{ty}[dst={_U32(dst)},src={_U32(src)},len={length}]", expected, wat


def _new_data(v):
    packed, src, length, expected = [
        (False, 0xFFFFFFFE, 4, "TRAP"),
        (False, 0xFFFFFFFF, 2, "TRAP"),
        (True, 0xFFFFFFFC, 1, "TRAP"),
        (False, 7, 2, "TRAP"),
        (False, 6, 2, "OK 2"),
        (True, 16, 0, "OK 0"),
        (True, 17, 0, "TRAP"),
    ][v % 7]
    ty = "i32" if packed else "i8"
    data = "\\01\\00\\00\\00\\02\\00\\00\\00\\03\\00\\00\\00\\04\\00\\00\\00" if packed else "ABCDEFGH"
    wat = f"""(module (type $a (array (mut {ty}))) (data $d "{data}")
  (func (export "f") (result i32)
    (array.len (array.new_data $a $d (i32.const {_U32(src)}) (i32.const {_U32(length)})))))"""
    return f"new-data-{ty}[src={_U32(src)},len={length}]", expected, wat


def _init_elem(v):
    dst, src, length, expected, read = [
        (0xFFFFFFFE, 0, 4, "TRAP", 0),
        (0, 0xFFFFFFFE, 4, "TRAP", 0),
        (0xFFFFFFFF, 0xFFFFFFFF, 2, "TRAP", 0),
        (3, 0, 2, "TRAP", 0),
        (2, 0, 2, "OK 0", 3),
        (4, 4, 0, "OK 1", 0),
        (5, 0, 0, "TRAP", 0),
    ][v % 7]
    wat = f"""(module (type $a (array (mut funcref)))
  (func $g) (elem $e func $g $g $g $g)
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 4)))
    (array.init_elem $a $e (local.get $a) (i32.const {_U32(dst)})
      (i32.const {_U32(src)}) (i32.const {_U32(length)}))
    (ref.is_null (array.get $a (local.get $a) (i32.const {read})))))"""
    return f"init-elem[dst={_U32(dst)},src={_U32(src)},len={length}]", expected, wat


def _new_elem(v):
    src, length, expected = [
        (0xFFFFFFFE, 4, "TRAP"),
        (0xFFFFFFFF, 2, "TRAP"),
        (0xFFFFFFFC, 8, "TRAP"),
        (3, 2, "TRAP"),
        (2, 2, "OK 2"),
        (4, 0, "OK 0"),
        (5, 0, "TRAP"),
    ][v % 7]
    wat = f"""(module (type $a (array (mut funcref)))
  (func $g) (elem $e func $g $g $g $g)
  (func (export "f") (result i32)
    (array.len (array.new_elem $a $e (i32.const {_U32(src)}) (i32.const {_U32(length)})))))"""
    return f"new-elem[src={_U32(src)},len={length}]", expected, wat


_FAMILIES = (_fill, _copy, _init_data, _new_data, _init_elem, _new_elem)


def arraywrap_gen(seed):
    """Return ``(label, export, expected, wat)`` for one GC-array extent probe."""
    family = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = family(seed // len(_FAMILIES))
    return f"arraywrap-{label}", "f", expected, wat
