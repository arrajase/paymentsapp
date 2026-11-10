"""Normalising and masking customer identifiers, and app PIN rules."""

import re
from dataclasses import dataclass
from typing import Literal

IdentifierKind = Literal["email", "mobile"]

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


@dataclass(frozen=True)
class Identifier:
    kind: IdentifierKind
    value: str  # normalised: lower-case email, or +91 followed by 10 digits
    masked: str  # safe to show and log


def normalise(kind: IdentifierKind, raw: str) -> Identifier:
    """Raise ValueError with a customer-facing reason if the identifier isn't valid."""
    if kind == "email":
        email = raw.strip().lower()
        if len(email) > 254 or not _EMAIL.match(email):
            raise ValueError("enter a valid email address")
        local, domain = email.split("@", 1)
        return Identifier("email", email, f"{local[:2]}•••@{domain}")

    digits = re.sub(r"[\s()-]", "", raw.strip())
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _INDIAN_MOBILE.match(digits):
        raise ValueError("enter a 10-digit Indian mobile number")
    return Identifier("mobile", f"+91{digits}", f"+91 {digits[:5]} •••{digits[-2:]}")


def pin_problem(pin: str) -> str | None:
    """A reason the app PIN is unacceptable, or None. Blocks the PINs people guess first."""
    if not (pin.isdigit() and 4 <= len(pin) <= 6):
        return "the app PIN must be 4 to 6 digits"
    if len(set(pin)) == 1:
        return "the app PIN can't be one digit repeated"
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:], strict=False)}
    if steps in ({1}, {-1}):
        return "the app PIN can't be a run of digits such as 1234"
    return None
