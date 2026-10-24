"""Password hashing, TOTP and opaque session tokens. Shared by admin sign-in now and customer sign-in later."""

import hashlib
import hmac
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with the library's defaults (RFC 9106 recommended parameters).
_hasher = PasswordHasher()

# Verified against when an account doesn't exist, so a wrong email takes as long as a wrong password and the
# response time doesn't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


MIN_PASSWORD_LENGTH = 12


def password_problem(password: str) -> str | None:
    """A reason the password is unacceptable, or None. Length over complexity rules (NIST SP 800-63B)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > 256:
        return "must be at most 256 characters"
    return None


# ---- TOTP (RFC 6238, 30-second steps, 6 digits) ----------------------------------------------------------------

TOTP_STEP_SECONDS = 30


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str, issuer: str = "paymentsapp admin") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def verify_totp(secret: str, code: str, last_used_step: int | None, now: float | None = None) -> int | None:
    """Return the time step the code belongs to, or None if it is wrong or was already used.

    One step either side is accepted for clock drift. A code's step must be later than the last one used, so a
    code seen over someone's shoulder can't be replayed within its 30 seconds.
    """
    if not (len(code) == 6 and code.isdigit()):
        return None
    current = int((time.time() if now is None else now) // TOTP_STEP_SECONDS)
    totp = pyotp.TOTP(secret)
    for step in (current - 1, current, current + 1):
        if hmac.compare_digest(totp.at(step * TOTP_STEP_SECONDS), code):
            if last_used_step is not None and step <= last_used_step:
                return None
            return step
    return None


# ---- Opaque session tokens -------------------------------------------------------------------------------------


def new_token(prefix: str) -> tuple[str, str]:
    """Return (token for the client, hash to store). Only the hash is stored, so a database leak yields no tokens."""
    token = f"{prefix}_{secrets.token_urlsafe(32)}"
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
