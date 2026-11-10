"""Device keys: the app holds an ECDSA P-256 private key (Android Keystore on a phone, a software key on the
emulator) and the server keeps the public key. Signing a fresh server challenge proves it's the same device.
"""

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class InvalidDeviceKey(ValueError):
    pass


def load_public_key(encoded: str) -> ec.EllipticCurvePublicKey:
    """Parse a base64 DER SubjectPublicKeyInfo and insist on ECDSA P-256."""
    try:
        key = serialization.load_der_public_key(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error, TypeError) as exc:
        raise InvalidDeviceKey("the device public key is not a valid DER public key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise InvalidDeviceKey("the device key must be ECDSA P-256")
    return key


def sign_in_message(challenge_id: str, nonce: str) -> bytes:
    """Exactly what the device signs. The prefix stops a signature made for anything else being reused here."""
    return f"paymentsapp-sign-in:{challenge_id}:{nonce}".encode()


def step_up_message(challenge_id: str, nonce: str, action: str) -> bytes:
    """What the device signs to confirm one sensitive action. Names the action, so it can't approve another."""
    return f"paymentsapp-step-up:{action}:{challenge_id}:{nonce}".encode()


def verify_signature(public_key: str, message: bytes, signature_b64: str) -> bool:
    try:
        key = load_public_key(public_key)
        key.verify(base64.b64decode(signature_b64, validate=True), message, ec.ECDSA(hashes.SHA256()))
    except (InvalidDeviceKey, InvalidSignature, ValueError, binascii.Error):
        return False
    return True
