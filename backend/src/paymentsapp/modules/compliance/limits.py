"""Wallet limits by KYC tier, read from config/compliance_limits.yaml (plan M8, 8.4).

The file is checked when it loads, so a typo can't quietly remove a limit: every tier must be listed, amounts
must be positive whole paise, and a tier with a wallet must say which kinds of payment it allows.
"""

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from paymentsapp.core.config import load_yaml_config

IST = ZoneInfo("Asia/Kolkata")
TIERS = ("T0", "T1", "T2")


@dataclass(frozen=True)
class TierLimits:
    wallet: bool
    max_balance: int | None = None
    max_load_per_month: int | None = None
    max_load_per_year: int | None = None
    merchant_payments: bool = False
    p2p_transfers: bool = False
    withdrawals: bool = False
    max_p2p_per_month: int | None = None


@dataclass(frozen=True)
class Limits:
    tiers: dict[str, TierLimits]
    min_per_transaction: int
    max_per_transaction: int
    new_device_max_per_transaction: int


def _amount(raw: Any, where: str, *, optional: bool = False) -> int | None:
    if raw is None and optional:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"compliance_limits.yaml: {where} must be a positive whole number of paise")
    return raw


def parse_limits(data: dict[str, Any]) -> Limits:
    tiers: dict[str, TierLimits] = {}
    raw_tiers = data.get("tiers") or {}
    for tier in TIERS:
        raw = raw_tiers.get(tier)
        if not isinstance(raw, dict) or not isinstance(raw.get("wallet"), bool):
            raise ValueError(f"compliance_limits.yaml: tier {tier} must be listed with wallet: true or false")
        if not raw["wallet"]:
            tiers[tier] = TierLimits(wallet=False)
            continue
        for flag in ("merchant_payments", "p2p_transfers", "withdrawals"):
            if not isinstance(raw.get(flag), bool):
                raise ValueError(f"compliance_limits.yaml: tiers.{tier}.{flag} must be true or false")
        tiers[tier] = TierLimits(
            wallet=True,
            max_balance=_amount(raw.get("max_balance"), f"tiers.{tier}.max_balance"),
            max_load_per_month=_amount(
                raw.get("max_load_per_month"), f"tiers.{tier}.max_load_per_month", optional=True
            ),
            max_load_per_year=_amount(raw.get("max_load_per_year"), f"tiers.{tier}.max_load_per_year", optional=True),
            merchant_payments=raw["merchant_payments"],
            p2p_transfers=raw["p2p_transfers"],
            withdrawals=raw["withdrawals"],
            max_p2p_per_month=_amount(raw.get("max_p2p_per_month"), f"tiers.{tier}.max_p2p_per_month", optional=True),
        )
    per_txn = data.get("per_transaction") or {}
    limits = Limits(
        tiers=tiers,
        min_per_transaction=_amount(per_txn.get("min"), "per_transaction.min") or 0,
        max_per_transaction=_amount(per_txn.get("max"), "per_transaction.max") or 0,
        new_device_max_per_transaction=_amount(
            data.get("new_device_max_per_transaction"), "new_device_max_per_transaction"
        )
        or 0,
    )
    if limits.min_per_transaction > limits.max_per_transaction:
        raise ValueError("compliance_limits.yaml: per_transaction.min is above per_transaction.max")
    return limits


@lru_cache
def get_limits() -> Limits:
    return parse_limits(load_yaml_config("compliance_limits"))


def month_start(now: datetime) -> datetime:
    """The start of the current calendar month in IST."""
    local = now.astimezone(IST)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def financial_year_start(now: datetime) -> datetime:
    """The start of the current financial year (1 April) in IST."""
    local = now.astimezone(IST)
    year = local.year if local.month >= 4 else local.year - 1
    return datetime.combine(date(year, 4, 1), datetime.min.time(), tzinfo=IST)


def rupees(paise: int) -> str:
    """For customer-facing messages: 1050 -> '₹10.50', 1000000 -> '₹10,000'."""
    whole, part = divmod(paise, 100)
    # Indian digit grouping: the last three digits, then pairs.
    digits = str(whole)
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"₹{grouped}" + (f".{part:02d}" if part else "")
