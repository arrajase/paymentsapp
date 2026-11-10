"""Name matching and masking for UPI linking (plan M3), and the step-up message (plan M1)."""

import pytest

from paymentsapp.core.errors import AppError
from paymentsapp.modules.identity.devices import sign_in_message, step_up_message
from paymentsapp.modules.upi.name_match import mask_name, mask_upi_id, match_score, names_match
from paymentsapp.modules.upi.service import normalise_upi_id


@pytest.mark.parametrize(
    ("kyc", "bank"),
    [
        ("Test User", "test user"),
        ("Arun Kumar", "KUMAR ARUN"),
        ("Arun Kumar", "Mr. Arun Kumar"),
        ("A Kumar", "Arun Kumar"),
        ("Asha Test", "Asha K Test"),  # a middle name the KYC left out
        ("Priya Sharma", "Priya Sharmaa"),  # a typo
        ("Smt. Lakshmi Iyer", "LAKSHMI IYER"),
    ],
)
def test_names_that_match(kyc: str, bank: str) -> None:
    assert names_match(kyc, bank), match_score(kyc, bank)


@pytest.mark.parametrize(
    ("kyc", "bank"),
    [
        ("Test User", "Test Person"),
        ("Arun Kumar", "Ravi Kumar"),
        ("Arun Kumar", "Kumar"),
        ("Asha Test", "Someone Else Entirely"),
        ("Test User", ""),
    ],
)
def test_names_that_do_not_match(kyc: str, bank: str) -> None:
    assert not names_match(kyc, bank), match_score(kyc, bank)


def test_masking() -> None:
    assert mask_name("test user") == "T••• U•••"
    assert mask_upi_id("test1@okmockbank") == "te•••1@okmockbank"
    assert mask_upi_id("ab@okmockbank") == "a•••@okmockbank"


def test_upi_ids_are_lower_cased_and_checked() -> None:
    assert normalise_upi_id("  Test1@OkMockBank ") == "test1@okmockbank"
    for bad in ["test1", "@okmockbank", "a b@bank", "x@1bank"]:
        with pytest.raises(AppError):
            normalise_upi_id(bad)


def test_step_up_signatures_name_their_action() -> None:
    link = step_up_message("c1", "n1", "upi_link")
    assert link != step_up_message("c1", "n1", "upi_unlink")
    assert link != sign_in_message("c1", "n1")
