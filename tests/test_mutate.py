"""Structural tests for the type-graph mutator."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.mutate import mutate_module


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def labels(wat):
    return {label for label, _ in mutate_module(wat, compound=0)}


def test_tag_use_mutations():
    wat = open(os.path.join(os.path.dirname(__file__), "..", "seeds", "eh_two_tags.wat")).read()
    labs = labels(wat)
    eq("mutates throw tag uses", "throw-tag@a->b" in labs, True)
    eq("mutates catch tag uses", "catch-tag@a->b" in labs, True)


def test_return_call_ref_slot_mutation():
    wat = """(module
      (type $a (sub (func (result i32))))
      (type $b (sub (func (result i32))))
      (func $f (type $a) (result i32) (i32.const 1))
      (elem declare func $f)
      (func (export "f") (result i32)
        (return_call_ref $a (ref.func $f))))"""
    labs = labels(wat)
    eq("mutates return_call_ref type slot", "return_call_ref@b" in labs, True)


def test_rec_rotate_and_reverse():
    wat = """(module
      (rec
        (type $a (struct (field i32)))
        (type $b (struct (field i32)))
        (type $c (struct (field i32))))
      (func (export "f") (result i32) (i32.const 0)))"""
    labs = labels(wat)
    eq("has rec rotation", "rotate-rec" in labs, True)
    eq("has rec reversal", "reverse-rec" in labs, True)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
