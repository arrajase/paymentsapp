"""Field encryption, password hashing, TOTP and session tokens."""

import pyotp
import pytest

from paymentsapp.core.crypto import DecryptionError, FieldCipher, generate_key
from paymentsapp.core.security import (
    TOTP_STEP_SECONDS,
    hash_password,
    hash_token,
    new_token,
    password_problem,
    verify_password,
    verify_totp,
)


def cipher(spec: str) -> FieldCipher:
    return FieldCipher.from_setting(spec)


def test_encrypt_round_trip_and_ciphertexts_differ() -> None:
    c = cipher(f"k1={generate_key()}")
    a = c.encrypt("JBSWY3DPEHPK3PXP", "admin_users.totp_secret:1")
    b = c.encrypt("JBSWY3DPEHPK3PXP", "admin_users.totp_secret:1")
    assert a != b  # random nonce
    assert "JBSWY3DP" not in a
    assert c.decrypt(a, "admin_users.totp_secret:1") == "JBSWY3DPEHPK3PXP"


def test_ciphertext_moved_to_another_row_fails() -> None:
    c = cipher(f"k1={generate_key()}")
    stored = c.encrypt("secret", "admin_users.totp_secret:1")
    with pytest.raises(DecryptionError):
        c.decrypt(stored, "admin_users.totp_secret:2")


def test_tampered_ciphertext_fails() -> None:
    c = cipher(f"k1={generate_key()}")
    stored = c.encrypt("secret", "ctx")
    flipped = stored[:-2] + ("A" if stored[-2] != "A" else "B") + stored[-1]
    with pytest.raises(DecryptionError):
        c.decrypt(flipped, "ctx")


def test_key_rotation_keeps_old_data_readable() -> None:
    old_key, new_key = generate_key(), generate_key()
    stored = cipher(f"k0={old_key}").encrypt("secret", "ctx")
    rotated = cipher(f"k1={new_key},k0={old_key}")
    assert rotated.decrypt(stored, "ctx") == "secret"
    assert rotated.needs_rotation(stored)
    assert not rotated.needs_rotation(rotated.encrypt("secret", "ctx"))


def test_unknown_key_id_fails() -> None:
    stored = cipher(f"k0={generate_key()}").encrypt("secret", "ctx")
    with pytest.raises(DecryptionError):
        cipher(f"k1={generate_key()}").decrypt(stored, "ctx")


@pytest.mark.parametrize("spec", ["", "noequals", "k1=c2hvcnQ"])
def test_bad_key_settings_are_rejected(spec: str) -> None:
    with pytest.raises(ValueError):
        cipher(spec)


def test_password_hashing() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong password here")
    assert not verify_password(None, "anything at all")  # unknown account
    assert not verify_password("not-a-hash", "anything at all")


def test_password_rules() -> None:
    assert password_problem("short") is not None
    assert password_problem("a" * 12) is None
    assert password_problem("a" * 257) is not None


def test_totp_accepts_current_code_once() -> None:
    secret = pyotp.random_base32()
    now = 1_800_000_000.0
    code = pyotp.TOTP(secret).at(now)

    step = verify_totp(secret, code, last_used_step=None, now=now)
    assert step == int(now // TOTP_STEP_SECONDS)
    assert verify_totp(secret, code, last_used_step=step, now=now) is None  # replay


def test_totp_allows_one_step_of_drift_only() -> None:
    secret = pyotp.random_base32()
    now = 1_800_000_000.0
    previous = pyotp.TOTP(secret).at(now - TOTP_STEP_SECONDS)
    stale = pyotp.TOTP(secret).at(now - 3 * TOTP_STEP_SECONDS)
    assert verify_totp(secret, previous, None, now=now) is not None
    assert verify_totp(secret, stale, None, now=now) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef"])
def test_totp_rejects_malformed_codes(code: str) -> None:
    assert verify_totp(pyotp.random_base32(), code, None) is None


def test_tokens_are_prefixed_random_and_stored_hashed() -> None:
    token, stored = new_token("adm")
    other, _ = new_token("adm")
    assert token.startswith("adm_") and token != other
    assert stored == hash_token(token) and token not in stored
