from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from typing import Any

from .errors import ApprovalRequired, InvalidTransition, SelfApprovalForbidden
from .models import (
    Actor,
    AuditRecord,
    CompensationRequest,
    ConfigurationVersion,
    DeliveryLog,
    Incident,
    MessageTemplate,
    NotificationRule,
    OperationsSnapshot,
    OutboundMessage,
    ProbeReading,
    SourceState,
    SupportTicket,
    TicketMessage,
)
from .ports import CompensationPort, NotificationProvider, ObservabilityProbe
from .repositories import (
    AuditTrail,
    InMemoryConfigurationRepository,
    InMemoryNotificationRepository,
    InMemoryOperationsRepository,
    InMemorySupportRepository,
    summary,
)

Clock = Callable[[], datetime]


class _Service:
    def __init__(self, *, clock: Clock | None = None, audit: AuditTrail | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.audit = audit or AuditTrail()
        self._ids = count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    def _audit(self, actor: Actor, action: str, kind: str, object_id: str, before: object, after: object) -> None:
        self.audit.append(
            AuditRecord(
                actor.tenant_id,
                actor.actor_id,
                actor.request_id,
                action,
                kind,
                object_id,
                summary(before),
                summary(after),
                "success",
                self._clock(),
            )
        )


class OperationsService(_Service):
    def __init__(
        self,
        repository: InMemoryOperationsRepository,
        *,
        service_probe: ObservabilityProbe | None = None,
        queue_probe: ObservabilityProbe | None = None,
        storage_probe: ObservabilityProbe | None = None,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        super().__init__(clock=clock, audit=audit)
        self.repository = repository
        self._probes = (service_probe, queue_probe, storage_probe)

    def _collect(
        self, probe: ObservabilityProbe | None, tenant_id: str, observed_at: datetime
    ) -> tuple[tuple[ProbeReading, ...], SourceState]:
        if probe is None:
            return (), SourceState("not_configured")
        try:
            return tuple(probe.collect(tenant_id, observed_at)), SourceState("available")
        except Exception as exc:  # infrastructure adapters define their own failures
            return (), SourceState("unavailable", type(exc).__name__)

    def capture_snapshot(self, actor: Actor) -> OperationsSnapshot:
        now = self._clock()
        service, service_state = self._collect(self._probes[0], actor.tenant_id, now)
        queue, queue_state = self._collect(self._probes[1], actor.tenant_id, now)
        storage, storage_state = self._collect(self._probes[2], actor.tenant_id, now)
        snapshot = OperationsSnapshot(
            actor.tenant_id,
            now,
            service,
            queue,
            storage,
            service_state,
            queue_state,
            storage_state,
        )
        self.repository.snapshots.append(snapshot)
        return snapshot

    def open_incident(
        self,
        actor: Actor,
        idempotency_key: str,
        source_type: str,
        source_id: str,
        severity: str,
        summary_text: str,
    ) -> Incident:
        fingerprint = (source_type, source_id, severity, summary_text)
        replay = self.repository.replay(actor.tenant_id, "open_incident", idempotency_key, fingerprint)
        if replay is not None:
            return replay
        now = self._clock()
        incident = Incident(
            self._id("incident"),
            actor.tenant_id,
            source_type,
            source_id,
            severity,
            summary_text,
            "open",
            None,
            None,
            1,
            now,
            now,
        )
        self.repository.put(actor.tenant_id, incident.id, incident)
        self.repository.remember(actor.tenant_id, "open_incident", idempotency_key, fingerprint, incident)
        self._audit(actor, "incident.open", "incident", incident.id, None, incident)
        return incident

    def acknowledge_incident(
        self, actor: Actor, idempotency_key: str, incident_id: str, version: int, owner_id: str
    ) -> Incident:
        return self._transition(actor, idempotency_key, incident_id, version, "acknowledged", owner_id, None)

    def resolve_incident(
        self, actor: Actor, idempotency_key: str, incident_id: str, version: int, resolution: str
    ) -> Incident:
        return self._transition(actor, idempotency_key, incident_id, version, "resolved", None, resolution)

    def _transition(
        self,
        actor: Actor,
        key: str,
        incident_id: str,
        version: int,
        status: str,
        owner_id: str | None,
        resolution: str | None,
    ) -> Incident:
        operation = f"incident.{status}"
        fingerprint = (incident_id, version, owner_id, resolution)
        replay = self.repository.replay(actor.tenant_id, operation, key, fingerprint)
        if replay is not None:
            return replay
        before = self.repository.get(actor.tenant_id, incident_id)
        if status == "resolved" and before.status not in {"open", "acknowledged"}:
            raise InvalidTransition(before.status)
        after = replace(
            before,
            status=status,
            owner_id=owner_id or before.owner_id,
            resolution=resolution,
            version=before.version + 1,
            updated_at=self._clock(),
        )
        self.repository.put_versioned(actor.tenant_id, incident_id, version, after)
        self.repository.remember(actor.tenant_id, operation, key, fingerprint, after)
        self._audit(actor, operation, "incident", incident_id, before, after)
        return after


class SupportService(_Service):
    def __init__(
        self,
        repository: InMemorySupportRepository,
        *,
        compensation_port: CompensationPort | None = None,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        super().__init__(clock=clock, audit=audit)
        self.repository = repository
        self.compensation_port = compensation_port

    def create_ticket(
        self, actor: Actor, key: str, category: str, related_object_id: str, priority: str, body: str
    ) -> SupportTicket:
        fingerprint = (category, related_object_id, priority, body)
        replay = self.repository.replay(actor.tenant_id, "ticket.create", key, fingerprint)
        if replay is not None:
            return replay
        now = self._clock()
        ticket = SupportTicket(
            self._id("ticket"),
            actor.tenant_id,
            category,
            related_object_id,
            priority,
            "open",
            None,
            None,
            None,
            (TicketMessage(actor.actor_id, body, now),),
            1,
            now,
            now,
        )
        self.repository.put(actor.tenant_id, ticket.id, ticket)
        self.repository.remember(actor.tenant_id, "ticket.create", key, fingerprint, ticket)
        self._audit(actor, "ticket.create", "support_ticket", ticket.id, None, ticket)
        return ticket

    def get_ticket(self, actor: Actor, ticket_id: str) -> SupportTicket:
        return self.repository.get(actor.tenant_id, ticket_id)

    def _update(
        self, actor: Actor, key: str, ticket_id: str, version: int, action: str, **changes: Any
    ) -> SupportTicket:
        fingerprint = (ticket_id, version, tuple(sorted(changes.items())))
        replay = self.repository.replay(actor.tenant_id, action, key, fingerprint)
        if replay is not None:
            return replay
        before = self.repository.get(actor.tenant_id, ticket_id)
        after = replace(before, **changes, version=before.version + 1, updated_at=self._clock())
        self.repository.put_versioned(actor.tenant_id, ticket_id, version, after)
        self.repository.remember(actor.tenant_id, action, key, fingerprint, after)
        self._audit(actor, action, "support_ticket", ticket_id, before, after)
        return after

    def reply(self, actor: Actor, key: str, ticket_id: str, version: int, body: str) -> SupportTicket:
        ticket = self.repository.get(actor.tenant_id, ticket_id)
        return self._update(
            actor,
            key,
            ticket_id,
            version,
            "ticket.reply",
            messages=(*ticket.messages, TicketMessage(actor.actor_id, body, self._clock())),
        )

    def assign(self, actor: Actor, key: str, ticket_id: str, version: int, assignee_id: str) -> SupportTicket:
        return self._update(actor, key, ticket_id, version, "ticket.assign", assignee_id=assignee_id, status="assigned")

    def escalate(self, actor: Actor, key: str, ticket_id: str, version: int, target: str) -> SupportTicket:
        return self._update(
            actor, key, ticket_id, version, "ticket.escalate", escalation_target=target, status="escalated"
        )

    def close(self, actor: Actor, key: str, ticket_id: str, version: int, reason: str) -> SupportTicket:
        return self._update(actor, key, ticket_id, version, "ticket.close", close_reason=reason, status="closed")

    def request_compensation(
        self,
        actor: Actor,
        key: str,
        ticket_id: str,
        ticket_version: int,
        kind: str,
        amount: int | Decimal,
        reason: str,
    ) -> CompensationRequest:
        self.repository.put_versioned(
            actor.tenant_id, ticket_id, ticket_version, self.repository.get(actor.tenant_id, ticket_id)
        )
        fingerprint = (ticket_id, ticket_version, kind, str(amount), reason)
        repo = self.repository.compensations
        replay = repo.replay(actor.tenant_id, "compensation.request", key, fingerprint)
        if replay is not None:
            return replay
        now = self._clock()
        request = CompensationRequest(
            self._id("compensation"),
            actor.tenant_id,
            ticket_id,
            kind,
            Decimal(amount),
            reason,
            actor.actor_id,
            None,
            "pending_approval",
            None,
            1,
            now,
            now,
        )
        repo.put(actor.tenant_id, request.id, request)
        repo.remember(actor.tenant_id, "compensation.request", key, fingerprint, request)
        self._audit(actor, "compensation.request", "compensation", request.id, None, request)
        return request

    def approve_compensation(self, actor: Actor, key: str, request_id: str, version: int) -> CompensationRequest:
        repo = self.repository.compensations
        before = repo.get(actor.tenant_id, request_id)
        if actor.actor_id == before.requested_by:
            raise SelfApprovalForbidden(request_id)
        fingerprint = (request_id, version)
        replay = repo.replay(actor.tenant_id, "compensation.approve", key, fingerprint)
        if replay is not None:
            return replay
        after = replace(
            before, approved_by=actor.actor_id, status="approved", version=before.version + 1, updated_at=self._clock()
        )
        repo.put_versioned(actor.tenant_id, request_id, version, after)
        repo.remember(actor.tenant_id, "compensation.approve", key, fingerprint, after)
        self._audit(actor, "compensation.approve", "compensation", request_id, before, after)
        return after

    def execute_compensation(self, actor: Actor, key: str, request_id: str, version: int) -> CompensationRequest:
        repo = self.repository.compensations
        fingerprint = (request_id, version)
        replay = repo.replay(actor.tenant_id, "compensation.execute", key, fingerprint)
        if replay is not None:
            return replay
        before = repo.get(actor.tenant_id, request_id)
        if before.status != "approved" or before.approved_by is None:
            raise ApprovalRequired(request_id)
        if before.version != version:
            from .errors import VersionConflict

            raise VersionConflict(request_id)
        if self.compensation_port is None:
            raise InvalidTransition("compensation port not configured")
        reference = self.compensation_port.execute(before)
        after = replace(
            before,
            status="completed",
            external_reference=reference,
            version=before.version + 1,
            updated_at=self._clock(),
        )
        repo.put_versioned(actor.tenant_id, request_id, version, after)
        repo.remember(actor.tenant_id, "compensation.execute", key, fingerprint, after)
        self._audit(actor, "compensation.execute", "compensation", request_id, before, after)
        return after


class NotificationService(_Service):
    def __init__(
        self,
        repository: InMemoryNotificationRepository,
        *,
        providers: Mapping[str, NotificationProvider] | None = None,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        super().__init__(clock=clock, audit=audit)
        self.repository = repository
        self.providers = dict(providers or {})

    def create_template(self, actor: Actor, key: str, name: str, channel: str, body: str) -> MessageTemplate:
        fingerprint = (name, channel, body)
        replay = self.repository.replay(actor.tenant_id, "template.create", key, fingerprint)
        if replay is not None:
            return replay  # type: ignore[return-value]
        template = MessageTemplate(
            self._id("template"), actor.tenant_id, name, channel, body, 1, "draft", 1, self._clock()
        )
        self.repository.templates[(actor.tenant_id, template.id, 1)] = template
        self.repository.remember(actor.tenant_id, "template.create", key, fingerprint, template)  # type: ignore[arg-type]
        self._audit(actor, "template.create", "message_template", template.id, None, template)
        return template

    def get_template(self, actor: Actor, template_id: str, template_version: int) -> MessageTemplate:
        return self.repository.templates[(actor.tenant_id, template_id, template_version)]

    def publish_template(self, actor: Actor, key: str, template_id: str, version: int) -> MessageTemplate:
        current = max(
            (
                template
                for (tenant, identity, _), template in self.repository.templates.items()
                if tenant == actor.tenant_id and identity == template_id
            ),
            key=lambda item: item.template_version,
        )
        fingerprint = (template_id, version)
        replay = self.repository.replay(actor.tenant_id, "template.publish", key, fingerprint)
        if replay is not None:
            return replay  # type: ignore[return-value]
        if current.version != version:
            from .errors import VersionConflict

            raise VersionConflict(template_id)
        published = replace(current, status="published", version=current.version + 1)
        self.repository.templates[(actor.tenant_id, template_id, current.template_version)] = published
        self.repository.remember(actor.tenant_id, "template.publish", key, fingerprint, published)  # type: ignore[arg-type]
        self._audit(actor, "template.publish", "message_template", template_id, current, published)
        return published

    def revise_template(self, actor: Actor, key: str, template_id: str, version: int, body: str) -> MessageTemplate:
        current = max(
            (
                template
                for (tenant, identity, _), template in self.repository.templates.items()
                if tenant == actor.tenant_id and identity == template_id
            ),
            key=lambda item: item.template_version,
        )
        if current.version != version:
            from .errors import VersionConflict

            raise VersionConflict(template_id)
        revised = MessageTemplate(
            template_id,
            actor.tenant_id,
            current.name,
            current.channel,
            body,
            current.template_version + 1,
            "draft",
            1,
            self._clock(),
        )
        self.repository.templates[(actor.tenant_id, template_id, revised.template_version)] = revised
        self._audit(actor, "template.revise", "message_template", template_id, current, revised)
        return revised

    def create_rule(
        self,
        actor: Actor,
        key: str,
        event_type: str,
        template_id: str,
        template_version: int,
        audience: Sequence[str],
        enabled: bool,
    ) -> NotificationRule:
        template = self.get_template(actor, template_id, template_version)
        if template.status != "published":
            raise InvalidTransition("notification rule requires a published template")
        fingerprint = (event_type, template_id, template_version, tuple(audience), enabled)
        replay = self.repository.replay(actor.tenant_id, "rule.create", key, fingerprint)
        if replay is not None:
            return replay
        rule = NotificationRule(
            self._id("rule"),
            actor.tenant_id,
            event_type,
            template_id,
            template_version,
            tuple(audience),
            enabled,
            1,
            self._clock(),
        )
        self.repository.put(actor.tenant_id, rule.id, rule)
        self.repository.remember(actor.tenant_id, "rule.create", key, fingerprint, rule)
        self._audit(actor, "rule.create", "notification_rule", rule.id, None, rule)
        return rule

    @staticmethod
    def _render(body: str, variables: Mapping[str, str]) -> str:
        required = set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", body))
        missing = required - variables.keys()
        if missing:
            raise ValueError(f"missing template variables: {', '.join(sorted(missing))}")
        return re.sub(r"{{\s*([a-zA-Z0-9_]+)\s*}}", lambda match: variables[match.group(1)], body)

    def deliver(
        self,
        actor: Actor,
        idempotency_key: str,
        rule_id: str,
        variables: Mapping[str, str],
        recipient_id: str,
    ) -> DeliveryLog:
        repo = self.repository.deliveries
        fingerprint = (rule_id, tuple(sorted(variables.items())), recipient_id)
        replay = repo.replay(actor.tenant_id, "notification.deliver", idempotency_key, fingerprint)
        if replay is not None:
            return replay
        rule = self.repository.get(actor.tenant_id, rule_id)
        if not rule.enabled:
            raise InvalidTransition("notification rule disabled")
        template = self.get_template(actor, rule.template_id, rule.template_version)
        now = self._clock()
        delivery_id = self._id("delivery")
        body = self._render(template.body, variables)
        provider = self.providers.get(template.channel)
        if provider is None:
            delivery = DeliveryLog(
                delivery_id,
                actor.tenant_id,
                rule_id,
                recipient_id,
                "pending",
                None,
                "provider_not_configured",
                now,
                now,
            )
        else:
            try:
                receipt = provider.send(
                    OutboundMessage(delivery_id, actor.tenant_id, template.channel, recipient_id, body)
                )
                delivery = DeliveryLog(
                    delivery_id,
                    actor.tenant_id,
                    rule_id,
                    recipient_id,
                    receipt.status,
                    receipt.provider_reference,
                    receipt.detail,
                    now,
                    self._clock(),
                )
            except Exception as exc:
                delivery = DeliveryLog(
                    delivery_id,
                    actor.tenant_id,
                    rule_id,
                    recipient_id,
                    "failed",
                    None,
                    type(exc).__name__,
                    now,
                    self._clock(),
                )
        repo.put(actor.tenant_id, delivery.id, delivery)
        repo.remember(actor.tenant_id, "notification.deliver", idempotency_key, fingerprint, delivery)
        self._audit(actor, "notification.deliver", "delivery", delivery.id, None, delivery)
        return delivery


class OperationsConfigurationService(_Service):
    def __init__(
        self,
        repository: InMemoryConfigurationRepository,
        *,
        clock: Clock | None = None,
        audit: AuditTrail | None = None,
    ) -> None:
        super().__init__(clock=clock, audit=audit)
        self.repository = repository

    def create_draft(self, actor: Actor, key: str, config_key: str, payload: Mapping[str, Any]) -> ConfigurationVersion:
        fingerprint = (config_key, tuple(sorted(payload.items())))
        replay = self.repository.replay(actor.tenant_id, "config.draft", key, fingerprint)
        if replay is not None:
            return replay
        versions = self.repository.by_key(actor.tenant_id, config_key)
        now = self._clock()
        draft = ConfigurationVersion(
            self._id("config"),
            actor.tenant_id,
            config_key,
            dict(payload),
            len(versions) + 1,
            "draft",
            actor.actor_id,
            None,
            None,
            1,
            now,
            now,
        )
        self.repository.put(actor.tenant_id, draft.id, draft)
        self.repository.remember(actor.tenant_id, "config.draft", key, fingerprint, draft)
        self._audit(actor, "config.draft", "configuration", draft.id, None, draft)
        return draft

    def approve(self, actor: Actor, key: str, config_id: str, version: int) -> ConfigurationVersion:
        before = self.repository.get(actor.tenant_id, config_id)
        if before.authored_by == actor.actor_id:
            raise SelfApprovalForbidden(config_id)
        return self._transition(actor, key, before, version, "approved", approved_by=actor.actor_id)

    def publish(self, actor: Actor, key: str, config_id: str, version: int) -> ConfigurationVersion:
        before = self.repository.get(actor.tenant_id, config_id)
        if before.status != "approved" or before.approved_by is None:
            raise ApprovalRequired(config_id)
        if before.version != version:
            from .errors import VersionConflict

            raise VersionConflict(config_id)
        for existing in self.repository.by_key(actor.tenant_id, before.key):
            if existing.status == "published" and existing.id != before.id:
                self.repository.put(
                    actor.tenant_id,
                    existing.id,
                    replace(existing, status="superseded", version=existing.version + 1, updated_at=self._clock()),
                )
        return self._transition(actor, key, before, version, "published")

    def rollback(
        self, actor: Actor, key: str, config_key: str, current_version: int, target_config_version: int
    ) -> ConfigurationVersion:
        versions = self.repository.by_key(actor.tenant_id, config_key)
        current = next(item for item in versions if item.status == "published")
        if current.config_version != current_version:
            from .errors import VersionConflict

            raise VersionConflict(config_key)
        target = next(item for item in versions if item.config_version == target_config_version)
        fingerprint = (config_key, current_version, target_config_version)
        replay = self.repository.replay(actor.tenant_id, "config.rollback", key, fingerprint)
        if replay is not None:
            return replay
        now = self._clock()
        draft = ConfigurationVersion(
            self._id("config"),
            actor.tenant_id,
            config_key,
            dict(target.payload),
            len(versions) + 1,
            "draft",
            actor.actor_id,
            None,
            target_config_version,
            1,
            now,
            now,
        )
        self.repository.put(actor.tenant_id, draft.id, draft)
        self.repository.remember(actor.tenant_id, "config.rollback", key, fingerprint, draft)
        self._audit(actor, "config.rollback", "configuration", draft.id, current, draft)
        return draft

    def _transition(
        self,
        actor: Actor,
        key: str,
        before: ConfigurationVersion,
        version: int,
        status: str,
        **changes: Any,
    ) -> ConfigurationVersion:
        operation = f"config.{status}"
        fingerprint = (before.id, version, tuple(sorted(changes.items())))
        replay = self.repository.replay(actor.tenant_id, operation, key, fingerprint)
        if replay is not None:
            return replay
        after = replace(before, status=status, **changes, version=before.version + 1, updated_at=self._clock())
        self.repository.put_versioned(actor.tenant_id, before.id, version, after)
        self.repository.remember(actor.tenant_id, operation, key, fingerprint, after)
        self._audit(actor, operation, "configuration", before.id, before, after)
        return after
