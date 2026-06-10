"""Ed25519 signing of sealed QPRs.

Requires the 'signing' extra (pip install veriqant-bench[signing]). The
signature covers the ASCII hex of integrity.content_sha256, so a signature
remains verifiable without re-canonicalizing the whole record.
"""

from __future__ import annotations

import base64

from ._generated import QuantumPerformanceRecord, Signature

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    _SIGNING_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _SIGNING_AVAILABLE = False


class SigningUnavailableError(RuntimeError):
    """Raised when signing/verification is requested without the 'signing' extra."""

    def __init__(self) -> None:
        super().__init__(
            "QPR signing requires the 'cryptography' package; "
            "install with: pip install veriqant-bench[signing]"
        )


def signing_available() -> bool:
    """True if the optional cryptography dependency is installed."""
    return _SIGNING_AVAILABLE


def generate_signing_key() -> bytes:
    """Generate a new Ed25519 private key (raw 32 bytes)."""
    if not _SIGNING_AVAILABLE:
        raise SigningUnavailableError()
    return Ed25519PrivateKey.generate().private_bytes_raw()


def sign_qpr(record: QuantumPerformanceRecord, private_key: bytes) -> QuantumPerformanceRecord:
    """Return a copy of *record* with integrity.signature populated.

    The record must already be sealed: the signature attests to the existing
    integrity.content_sha256.
    """
    if not _SIGNING_AVAILABLE:
        raise SigningUnavailableError()
    key = Ed25519PrivateKey.from_private_bytes(private_key)
    message = record.integrity.content_sha256.encode("ascii")
    signature = Signature(
        algorithm="ed25519",
        public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii"),
        value=base64.b64encode(key.sign(message)).decode("ascii"),
    )
    integrity = record.integrity.model_copy(update={"signature": signature})
    return record.model_copy(update={"integrity": integrity})


def verify_signature(record: QuantumPerformanceRecord) -> bool:
    """Check the record's signature against its content hash and embedded key.

    Returns False for a missing or invalid signature. Note this only proves
    the record was signed by the holder of the embedded key; trusting that key
    is the caller's policy decision.
    """
    if not _SIGNING_AVAILABLE:
        raise SigningUnavailableError()
    signature = record.integrity.signature
    if signature is None:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(signature.public_key))
        public_key.verify(
            base64.b64decode(signature.value),
            record.integrity.content_sha256.encode("ascii"),
        )
    except (InvalidSignature, ValueError):
        return False
    return True
