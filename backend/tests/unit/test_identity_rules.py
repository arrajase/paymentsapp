"""Identifier normalising and masking, app PIN rules, device signatures and age (plan M1, M2)."""

import base64
from datetime import date

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from paymentsapp.modules.identity.devices import InvalidDeviceKey, load_public_key, sign_in_message, verify_signature
from paymentsapp.modules.identity.identifiers import normalise, pin_problem
from paymentsapp.modules.kyc.service import age_on


@pytest.mark.parametrize(
    "raw", ["9000000001", "+91 90000 00001", "+919000000001", "919000000001", "09000000001", "90000-00001"]
)
def test_mobile_numbers_normalise_to_one_form(raw: str) -> None:
    identifier = normalise("mobile", raw)
    assert identifier.value == "+919000000001"
    assert identifier.masked == "+91 90000 •••01"


@pytest.mark.parametrize("raw", ["12345", "5000000001", "+1 415 555 0100", "90000 0000"])
def test_bad_mobile_numbers_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        normalise("mobile", raw)


def test_email_is_lower_cased_and_masked() -> None:
    identifier = normalise("email", "  Asha.Test@Example.COM ")
    assert identifier.value == "asha.test@example.com"
    assert identifier.masked == "as•••@example.com"


@pytest.mark.parametrize("raw", ["no-at-sign", "a@b", "two@@example.com", "spaces in@example.com"])
def test_bad_emails_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        normalise("email", raw)


@pytest.mark.parametrize("pin", ["2580", "13579", "804215"])
def test_reasonable_pins_are_accepted(pin: str) -> None:
    assert pin_problem(pin) is None


@pytest.mark.parametrize("pin", ["123", "1234567", "12a4", "1111", "000000", "1234", "4321", "987654"])
def test_weak_or_malformed_pins_are_refused(pin: str) -> None:
    assert pin_problem(pin) is not None


def _keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(der).decode()


def test_device_signature_round_trip() -> None:
    private, public = _keypair()
    message = sign_in_message("c1", "nonce")
    signature = base64.b64encode(private.sign(message, ec.ECDSA(hashes.SHA256()))).decode()
    assert verify_signature(public, message, signature)
    assert not verify_signature(public, sign_in_message("c1", "other-nonce"), signature)
    _, other_public = _keypair()
    assert not verify_signature(other_public, message, signature)
    assert not verify_signature(public, message, "not base64!!")


def test_only_p256_keys_are_accepted() -> None:
    rsa_der = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    p384_der = (
        ec.generate_private_key(ec.SECP384R1())
        .public_key()
        .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    for der in (rsa_der, p384_der):
        with pytest.raises(InvalidDeviceKey):
            load_public_key(base64.b64encode(der).decode())
    with pytest.raises(InvalidDeviceKey):
        load_public_key("not-a-key")


@pytest.mark.parametrize(
    ("born", "today", "age"),
    [
        (date(2008, 10, 24), date(2026, 10, 24), 18),  # 18th birthday today
        (date(2008, 10, 25), date(2026, 10, 24), 17),  # turns 18 tomorrow
        (date(2004, 2, 29), date(2026, 2, 28), 21),
    ],
)
def test_age(born: date, today: date, age: int) -> None:
    assert age_on(born, today) == age
