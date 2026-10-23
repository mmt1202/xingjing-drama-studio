"""A signed gateway boundary never treats transport uncertainty as settlement."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .contracts import Channel, Offer, PaymentFailure, PaymentSettings, minor


class PaymentGateway(Protocol):
    async def command(self, operation: str, payload: dict[str, Any], key: str) -> dict[str, Any]: ...

    def verify_event(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]: ...


class RefundApprovalVerifier(Protocol):
    async def verify(
        self, *, context: Any, refund_id: str, amount_minor: int, approval_basis_minor: int, request: Any
    ) -> bool: ...


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def checkout_url(value: object, settings: PaymentSettings) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise PaymentFailure("PAYMENT_CHECKOUT_INVALID", 502)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise PaymentFailure("PAYMENT_CHECKOUT_INVALID", 502) from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.casefold() not in settings.checkout_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise PaymentFailure("PAYMENT_CHECKOUT_INVALID", 502)
    return value


def load_settings() -> PaymentSettings | None:
    names = (
        "XINGJING_PAYMENT_GATEWAY_URL",
        "XINGJING_PAYMENT_MERCHANT_ID",
        "XINGJING_PAYMENT_PROVIDER",
        "XINGJING_PAYMENT_GATEWAY_SECRET",
        "XINGJING_PAYMENT_CHECKOUT_HOSTS",
        "XINGJING_PAYMENT_OFFERS_JSON",
        "XINGJING_PAYMENT_CHANNELS_JSON",
    )
    values = [os.environ.get(name, "").strip() for name in names]
    if not all(values):
        return None
    base, merchant, provider, secret, hosts, raw_offers, raw_channels = values
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or len(secret) < 32
            or len(merchant) > 128
            or len(provider) > 128
        ):
            raise ValueError("invalid fixed gateway endpoint")
        offers = tuple(
            Offer(
                str(item["id"]),
                str(item["label"]),
                minor(item["amountMinor"]),
                minor(item["creditMinor"]),
                str(item["currency"]).upper(),
            )
            for item in json.loads(raw_offers)
        )
        channels = tuple(Channel(str(item["id"]), str(item["label"])) for item in json.loads(raw_channels))
        if (
            not offers
            or not channels
            or len(offers) > 100
            or len(channels) > 32
            or len({item.id for item in offers}) != len(offers)
            or len({item.id for item in channels}) != len(channels)
            or len({item.currency for item in offers}) != 1
            or any(
                item.amount_minor != item.credit_minor
                or len(item.currency) != 3
                or not item.currency.isascii()
                or not item.currency.isalpha()
                for item in offers
            )
            or any(
                not item.id or len(item.id) > 128 or not item.label or len(item.label) > 160
                for item in (*offers, *channels)
            )
        ):
            raise ValueError("invalid configured offers")
        threshold = os.environ.get("XINGJING_PAYMENT_REFUND_MFA_THRESHOLD_MINOR", "").strip()
        settings = PaymentSettings(
            merchant,
            provider,
            base.rstrip("/"),
            secret,
            frozenset(host.strip().casefold() for host in hosts.split(",") if host.strip()),
            offers,
            channels,
            refund_mfa_threshold_minor=minor(threshold) if threshold else None,
        )
        if not settings.checkout_hosts:
            raise ValueError("checkout hosts missing")
        return settings
    except (ValueError, KeyError, TypeError, PaymentFailure) as error:
        raise PaymentFailure("PAYMENT_CONFIGURATION_INVALID", 503) from error


class SignedHttpPaymentGateway:
    def __init__(self, settings: PaymentSettings, *, client: httpx.AsyncClient | None = None):
        self.settings, self._client = settings, client

    def _signature(self, timestamp: str, nonce: str, raw: bytes) -> str:
        return hmac.new(
            self.settings.secret.encode(), timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw, hashlib.sha256
        ).hexdigest()

    def verify_event(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        if len(raw) > 65536:
            raise PaymentFailure("PAYMENT_GATEWAY_MESSAGE_TOO_LARGE", 413)
        timestamp, nonce = headers.get("x-payment-timestamp", ""), headers.get("x-payment-nonce", "")
        signature = headers.get("x-payment-signature", "").removeprefix("sha256=")
        try:
            valid_time = abs(time.time() - int(timestamp)) <= 300
        except ValueError:
            valid_time = False
        if (
            not valid_time
            or not nonce
            or len(nonce) > 128
            or not hmac.compare_digest(signature, self._signature(timestamp, nonce, raw))
        ):
            raise PaymentFailure("PAYMENT_GATEWAY_SIGNATURE_INVALID", 403)
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as error:
            raise PaymentFailure("PAYMENT_GATEWAY_MESSAGE_INVALID", 422) from error
        if not isinstance(body, dict):
            raise PaymentFailure("PAYMENT_GATEWAY_MESSAGE_INVALID", 422)
        return body

    async def command(self, operation: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raw = canonical({"operation": operation, **payload})
        timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
            "X-Payment-Timestamp": timestamp,
            "X-Payment-Nonce": nonce,
            "X-Payment-Signature": self._signature(timestamp, nonce, raw),
        }
        owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        try:
            async with client.stream(
                "POST", self.settings.base_url + "/v1/commands", content=raw, headers=headers
            ) as response:
                if response.status_code != 200:
                    raise PaymentFailure("PAYMENT_GATEWAY_UNCERTAIN", 503)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise PaymentFailure("PAYMENT_GATEWAY_MESSAGE_TOO_LARGE", 413)
                body = self.verify_event(bytes(data), response.headers)
            if body.get("requestKey") != key:
                raise PaymentFailure("PAYMENT_GATEWAY_RESPONSE_MISMATCH", 502)
            if not isinstance(body.get("event"), dict):
                raise PaymentFailure("PAYMENT_GATEWAY_RESPONSE_MISMATCH", 502)
            return body["event"]
        except httpx.HTTPError as error:
            raise PaymentFailure("PAYMENT_GATEWAY_UNCERTAIN", 503) from error
        finally:
            if owned:
                await client.aclose()
