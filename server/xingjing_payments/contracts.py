"""Payment amounts retain the existing monetary minor-unit ledger semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_MINOR = 9_223_372_036_854_775_807
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Minor = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,18}$")]


class PaymentFailure(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class CreateOrder(StrictModel):
    offerId: Identifier
    channel: Identifier
    reason: str = Field(min_length=1, max_length=1000)


class OrderAction(StrictModel):
    action: Literal["refresh", "cancel", "requestRefund"]
    expectedVersion: int = Field(ge=1)
    amountMinor: Minor | None = None
    reason: str | None = Field(None, min_length=1, max_length=1000)
    ticketId: str | None = Field(None, min_length=1, max_length=128)


class RefundAction(StrictModel):
    action: Literal["approve", "reject", "refresh"]
    expectedVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


@dataclass(frozen=True)
class Offer:
    id: str
    label: str
    amount_minor: int
    credit_minor: int
    currency: str


@dataclass(frozen=True)
class Channel:
    id: str
    label: str


@dataclass(frozen=True)
class PaymentSettings:
    merchant_id: str
    provider: str
    base_url: str
    secret: str
    checkout_hosts: frozenset[str]
    offers: tuple[Offer, ...]
    channels: tuple[Channel, ...]
    expiry_seconds: int = 1800
    refund_mfa_threshold_minor: int | None = None


def minor(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PaymentFailure("PAYMENT_AMOUNT_INVALID", 422)
    if isinstance(value, str) and (not value.isascii() or not value.isdecimal() or str(int(value)) != value):
        raise PaymentFailure("PAYMENT_AMOUNT_INVALID", 422)
    result = int(value)
    if result < 1 or result > MAX_MINOR:
        raise PaymentFailure("PAYMENT_AMOUNT_INVALID", 422)
    return result
