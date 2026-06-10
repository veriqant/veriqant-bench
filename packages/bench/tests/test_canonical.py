from __future__ import annotations

import math

import pytest

from veriqant_bench.qpr import canonical_json, content_sha256, sha256_hex


def test_keys_sorted_recursively_and_compact() -> None:
    value = {"b": 1, "a": {"d": [1, 2], "c": True}}
    assert canonical_json(value) == '{"a":{"c":true,"d":[1,2]},"b":1}'


def test_non_ascii_is_not_escaped() -> None:
    assert canonical_json({"name": "Schrödinger résonance"}) == '{"name":"Schrödinger résonance"}'


def test_nan_and_infinity_are_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})
    with pytest.raises(ValueError):
        canonical_json({"x": math.inf})


def test_sha256_hex_known_vector() -> None:
    # sha256("abc") test vector from FIPS 180-2.
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_content_sha256_ignores_integrity_member() -> None:
    base = {"qpr_version": "0.1.0", "data": [1, 2, 3]}
    with_integrity = {**base, "integrity": {"content_sha256": "f" * 64}}
    assert content_sha256(base) == content_sha256(with_integrity)


def test_content_sha256_is_key_order_independent() -> None:
    forward = {"a": 1, "b": {"x": 1, "y": 2}}
    backward = {"b": {"y": 2, "x": 1}, "a": 1}
    assert content_sha256(forward) == content_sha256(backward)
