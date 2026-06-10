"""Canonical JSON serialization and content hashing for QPRs.

The canonical form is defined in docs/QPR-SPEC.md §Canonicalization: UTF-8,
lexicographically sorted object keys, compact separators, no NaN/Infinity,
non-ASCII characters unescaped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

INTEGRITY_KEY = "integrity"


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value to QPR canonical JSON."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(text: str) -> str:
    """Lowercase hex SHA-256 of the UTF-8 encoding of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_sha256(document: Mapping[str, Any]) -> str:
    """Content hash of a QPR document.

    SHA-256 over the canonical JSON of the document with its top-level
    'integrity' member removed. This is the value recorded in (and verified
    against) integrity.content_sha256.
    """
    body = {key: value for key, value in document.items() if key != INTEGRITY_KEY}
    return sha256_hex(canonical_json(body))
