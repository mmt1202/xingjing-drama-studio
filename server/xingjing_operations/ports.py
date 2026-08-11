from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from .models import CompensationRequest, DeliveryReceipt, OutboundMessage, ProbeReading


class ObservabilityProbe(Protocol):
    def collect(self, tenant_id: str, observed_at: datetime) -> Sequence[ProbeReading]: ...


class CompensationPort(Protocol):
    def execute(self, request: CompensationRequest) -> str: ...


class NotificationProvider(Protocol):
    def send(self, message: OutboundMessage) -> DeliveryReceipt: ...
