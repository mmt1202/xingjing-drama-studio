from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from .errors import (
    AccountingRejected,
    IdempotencyConflict,
    InvalidTransition,
    OrderNotFound,
    PermissionDenied,
    SettlementBlocked,
    ValidationError,
    VersionConflict,
)
from .ids import new_uuid7
from .models import (
    AcceptanceDecision,
    AcceptanceRecord,
    AccountingAction,
    AccountingReceipt,
    AccountingRequest,
    Actor,
    AuditEvent,
    CommandRecord,
    CommercialDelivery,
    CommercialDispute,
    CommercialMilestone,
    CommercialOrder,
    CommercialQuote,
    ContractVersion,
    DeliveryStatus,
    DisputeStatus,
    JsonObject,
    MilestoneInput,
    MilestoneStatus,
    OrderStatus,
    QuoteStatus,
    Settlement,
    SettlementStatus,
)
from .ports import AccountingPort, CommercialRepository, CommercialUnitOfWork, DeliveryArtifactPort


class CommercialService:
    def __init__(
        self,
        repository: CommercialRepository,
        accounting: AccountingPort,
        *,
        delivery_artifacts: DeliveryArtifactPort | None = None,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.accounting = accounting
        self.delivery_artifacts = delivery_artifacts
        self.now = now or (lambda: datetime.now(UTC))
        self.id_factory = id_factory or new_uuid7

    @staticmethod
    def _require_permission(actor: Actor, permission: str) -> None:
        if permission not in actor.permissions:
            raise PermissionDenied()

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be blank")
        return normalized

    @staticmethod
    def _positive_minor(value: int, field: str) -> int:
        if type(value) is not int or value <= 0:
            raise ValidationError(f"{field} must be a positive integer in minor units")
        return value

    @staticmethod
    def _fingerprint(payload: JsonObject) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _audit(
        self,
        uow: CommercialUnitOfWork,
        *,
        event_type: str,
        actor: Actor,
        workspace_id: str,
        object_id: str,
        request_id: str,
        before: CommercialOrder | None,
        after: CommercialOrder,
    ) -> None:
        uow.append_audit(
            AuditEvent(
                event_id=self.id_factory(),
                event_type=event_type,
                occurred_at=self.now(),
                actor_id=actor.actor_id,
                workspace_id=workspace_id,
                object_type="commercial_order",
                object_id=object_id,
                request_id=self._required_text(request_id, "request_id"),
                before=before.to_dict() if before is not None else None,
                after=after.to_dict(),
            )
        )

    def _replay(
        self,
        uow: CommercialUnitOfWork,
        *,
        workspace_id: str,
        scope: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> CommercialOrder | None:
        cached = uow.get_command(workspace_id, scope, idempotency_key)
        if cached is None:
            return None
        if cached.fingerprint != fingerprint:
            raise IdempotencyConflict()
        value = json.loads(cached.result_json)
        if not isinstance(value, dict):
            raise TypeError("stored command result is not an object")
        return CommercialOrder.from_dict(value)

    def _record_command(
        self,
        uow: CommercialUnitOfWork,
        *,
        workspace_id: str,
        scope: str,
        idempotency_key: str,
        fingerprint: str,
        result: CommercialOrder,
    ) -> None:
        uow.put_command(
            CommandRecord(
                workspace_id,
                scope,
                idempotency_key,
                fingerprint,
                json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                self.now(),
            )
        )

    @staticmethod
    def _require_owner_manage(actor: Actor, order: CommercialOrder) -> None:
        member_access = "commercial.manage" in actor.permissions and actor.workspace_id == order.owner_workspace_id
        admin_access = (
            "admin.commercial.manage" in actor.permissions
            and order.owner_workspace_id in actor.data_scope_workspace_ids
        )
        if not (member_access or admin_access):
            raise OrderNotFound()

    def publish_order(
        self,
        *,
        actor: Actor,
        title: str,
        requirements: str,
        budget_minor: int,
        currency: str,
        milestones: Sequence[MilestoneInput],
        request_id: str,
        idempotency_key: str,
        owner_workspace_id: str | None = None,
    ) -> CommercialOrder:
        target_workspace_id = (owner_workspace_id or actor.workspace_id).strip()
        member_access = "commercial.manage" in actor.permissions and target_workspace_id == actor.workspace_id
        admin_access = (
            "admin.commercial.manage" in actor.permissions
            and target_workspace_id in actor.data_scope_workspace_ids
        )
        if not (member_access or admin_access):
            raise PermissionDenied()
        title = self._required_text(title, "title")
        requirements = self._required_text(requirements, "requirements")
        budget_minor = self._positive_minor(budget_minor, "budget_minor")
        currency = self._required_text(currency, "currency").upper()
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        if not milestones:
            raise ValidationError("at least one milestone is required")
        normalized_milestones = tuple(
            MilestoneInput(
                self._required_text(item.title, "milestone.title"),
                self._positive_minor(item.amount_minor, "milestone.amount_minor"),
                self._required_text(item.acceptance_criteria, "milestone.acceptance_criteria"),
                item.due_at,
            )
            for item in milestones
        )
        if sum(item.amount_minor for item in normalized_milestones) != budget_minor:
            raise ValidationError("milestone amounts must equal the order budget")
        payload: JsonObject = {
            "actor_id": actor.actor_id,
            "owner_workspace_id": target_workspace_id,
            "title": title,
            "requirements": requirements,
            "budget_minor": budget_minor,
            "currency": currency,
            "milestones": [item.to_dict() for item in normalized_milestones],
        }
        fingerprint = self._fingerprint(payload)
        scope = "commercial.order.publish"
        with self.repository.atomic() as uow:
            cached = uow.get_command(target_workspace_id, scope, idempotency_key)
            if cached is not None:
                if cached.fingerprint != fingerprint:
                    raise IdempotencyConflict()
                value = json.loads(cached.result_json)
                if not isinstance(value, dict):
                    raise TypeError("stored command result is not an object")
                return CommercialOrder.from_dict(value)
            timestamp = self.now()
            order = CommercialOrder(
                id=self.id_factory(),
                owner_workspace_id=target_workspace_id,
                title=title,
                requirements=requirements,
                budget_minor=budget_minor,
                currency=currency,
                milestones=tuple(
                    CommercialMilestone(
                        id=self.id_factory(),
                        title=item.title,
                        amount_minor=item.amount_minor,
                        acceptance_criteria=item.acceptance_criteria,
                        due_at=item.due_at,
                        status=MilestoneStatus.PENDING,
                    )
                    for item in normalized_milestones
                ),
                status=OrderStatus.PUBLISHED,
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            uow.put_order(order, expected_version=None)
            uow.put_command(
                CommandRecord(
                    target_workspace_id,
                    scope,
                    idempotency_key,
                    fingerprint,
                    json.dumps(order.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    timestamp,
                )
            )
            self._audit(
                uow,
                event_type="commercial.order.published",
                actor=actor,
                workspace_id=actor.workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=None,
                after=order,
            )
            return order

    def audit_events(self, actor: Actor, owner_workspace_id: str, order_id: str) -> tuple[AuditEvent, ...]:
        if not ({"commercial.view", "admin.commercial.view"} & actor.permissions):
            raise PermissionDenied()
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None or not self._can_view(actor, order):
                raise OrderNotFound()
            return uow.list_audit(owner_workspace_id, order_id)

    def get_order(self, actor: Actor, owner_workspace_id: str, order_id: str) -> CommercialOrder:
        if not ({"commercial.view", "admin.commercial.view"} & actor.permissions):
            raise PermissionDenied()
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None or not self._can_view(actor, order):
                raise OrderNotFound()
            return order

    def submit_quote(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        amount_minor: int,
        currency: str,
        proposal: str,
        valid_until: datetime,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        self._require_permission(actor, "commercial.manage")
        amount_minor = self._positive_minor(amount_minor, "amount_minor")
        currency = self._required_text(currency, "currency").upper()
        proposal = self._required_text(proposal, "proposal")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        if valid_until <= self.now():
            raise ValidationError("valid_until must be in the future")
        scope = f"commercial.quote.submit:{order_id}:{actor.workspace_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "amount_minor": amount_minor,
                "currency": currency,
                "proposal": proposal,
                "valid_until": valid_until.isoformat(),
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if actor.workspace_id == owner_workspace_id:
                raise InvalidTransition("order owners cannot quote their own order")
            if order.status is not OrderStatus.PUBLISHED:
                raise InvalidTransition("quotes are accepted only while an order is published")
            if currency != order.currency:
                raise ValidationError("quote currency must match the order currency")
            if order.version != expected_version:
                raise VersionConflict()
            quote = CommercialQuote(
                id=self.id_factory(),
                contractor_workspace_id=actor.workspace_id,
                submitted_by=actor.actor_id,
                amount_minor=amount_minor,
                currency=currency,
                proposal=proposal,
                valid_until=valid_until,
                status=QuoteStatus.SUBMITTED,
                version=1,
                created_at=self.now(),
            )
            updated = replace(
                order,
                quotes=(*order.quotes, quote),
                version=order.version + 1,
                updated_at=self.now(),
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type="commercial.quote.submitted",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def accept_quote(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        quote_id: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        if not ({"commercial.manage", "admin.commercial.manage"} & actor.permissions):
            raise PermissionDenied()
        quote_id = self._required_text(quote_id, "quote_id")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.quote.accept:{order_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "quote_id": quote_id,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            self._require_owner_manage(actor, order)
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if order.status is not OrderStatus.PUBLISHED:
                raise InvalidTransition("the order is no longer accepting a quote")
            if order.version != expected_version:
                raise VersionConflict()
            selected = next((quote for quote in order.quotes if quote.id == quote_id), None)
            if selected is None or selected.status is not QuoteStatus.SUBMITTED or selected.valid_until <= self.now():
                raise InvalidTransition("quote is unavailable")
            quotes = tuple(
                replace(
                    quote,
                    status=QuoteStatus.ACCEPTED if quote.id == quote_id else QuoteStatus.DECLINED,
                    version=quote.version + 1,
                )
                if quote.status is QuoteStatus.SUBMITTED
                else quote
                for quote in order.quotes
            )
            updated = replace(
                order,
                quotes=quotes,
                contractor_workspace_id=selected.contractor_workspace_id,
                accepted_quote_id=selected.id,
                status=OrderStatus.AWARDED,
                version=order.version + 1,
                updated_at=self.now(),
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type="commercial.quote.accepted",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def record_contract_version(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        content_ref: str,
        content_digest: str,
        amount_minor: int,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        if not ({"commercial.manage", "admin.commercial.manage"} & actor.permissions):
            raise PermissionDenied()
        content_ref = self._required_text(content_ref, "content_ref")
        content_digest = self._required_text(content_digest, "content_digest")
        amount_minor = self._positive_minor(amount_minor, "amount_minor")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.contract.record:{order_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "content_ref": content_ref,
                "content_digest": content_digest,
                "amount_minor": amount_minor,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            self._require_owner_manage(actor, order)
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if order.status not in (OrderStatus.AWARDED, OrderStatus.CONTRACTED) or order.accepted_quote_id is None:
                raise InvalidTransition("a quote must be accepted before recording a contract")
            if order.version != expected_version:
                raise VersionConflict()
            accepted_quote = next(quote for quote in order.quotes if quote.id == order.accepted_quote_id)
            if amount_minor != accepted_quote.amount_minor:
                raise ValidationError("contract amount must equal the accepted quote")
            contract = ContractVersion(
                id=self.id_factory(),
                sequence=len(order.contract_versions) + 1,
                quote_id=accepted_quote.id,
                content_ref=content_ref,
                content_digest=content_digest,
                amount_minor=amount_minor,
                currency=accepted_quote.currency,
                created_by=actor.actor_id,
                created_at=self.now(),
            )
            updated = replace(
                order,
                contract_versions=(*order.contract_versions, contract),
                active_contract_version_id=contract.id,
                status=OrderStatus.CONTRACTED,
                version=order.version + 1,
                updated_at=self.now(),
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type="commercial.contract.version_recorded",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def submit_delivery(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        milestone_id: str,
        artifact_version_id: str,
        artifact_digest: str,
        note: str | None,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        self._require_permission(actor, "commercial.manage")
        milestone_id = self._required_text(milestone_id, "milestone_id")
        artifact_version_id = self._required_text(artifact_version_id, "artifact_version_id")
        artifact_digest = self._required_text(artifact_digest, "artifact_digest")
        normalized_note = note.strip() if note is not None and note.strip() else None
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.delivery.submit:{order_id}:{milestone_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "artifact_version_id": artifact_version_id,
                "artifact_digest": artifact_digest,
                "note": normalized_note,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            if actor.workspace_id != order.contractor_workspace_id:
                raise OrderNotFound()
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if self.delivery_artifacts is None:
                raise ValidationError("delivery artifact catalog is not configured")
            if not self.delivery_artifacts.verify_selected_artifact(
                workspace_id=actor.workspace_id,
                artifact_version_id=artifact_version_id,
                artifact_digest=artifact_digest,
            ):
                raise ValidationError("delivery artifact is unavailable, unselected, or has a mismatched digest")
            if order.status not in (OrderStatus.CONTRACTED, OrderStatus.IN_DELIVERY):
                raise InvalidTransition("the order has no active delivery contract")
            if order.active_contract_version_id is None:
                raise InvalidTransition("delivery must bind an active contract version")
            if order.version != expected_version:
                raise VersionConflict()
            milestone = next((item for item in order.milestones if item.id == milestone_id), None)
            if milestone is None:
                raise ValidationError("milestone does not exist")
            if milestone.status not in (MilestoneStatus.PENDING, MilestoneStatus.CHANGES_REQUESTED):
                raise InvalidTransition("milestone is not open for delivery")
            revision = 1 + max(
                (delivery.revision for delivery in order.deliveries if delivery.milestone_id == milestone_id),
                default=0,
            )
            timestamp = self.now()
            delivery = CommercialDelivery(
                id=self.id_factory(),
                milestone_id=milestone_id,
                revision=revision,
                contract_version_id=order.active_contract_version_id,
                artifact_version_id=artifact_version_id,
                artifact_digest=artifact_digest,
                note=normalized_note,
                submitted_by=actor.actor_id,
                status=DeliveryStatus.SUBMITTED,
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            milestones = tuple(
                replace(item, status=MilestoneStatus.DELIVERED, version=item.version + 1)
                if item.id == milestone_id
                else item
                for item in order.milestones
            )
            updated = replace(
                order,
                deliveries=(*order.deliveries, delivery),
                milestones=milestones,
                status=OrderStatus.IN_DELIVERY,
                version=order.version + 1,
                updated_at=timestamp,
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type="commercial.delivery.submitted",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def decide_delivery(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        delivery_id: str,
        decision: AcceptanceDecision,
        reason: str | None,
        evidence_ref: str | None,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        if not ({"commercial.manage", "admin.commercial.manage"} & actor.permissions):
            raise PermissionDenied()
        delivery_id = self._required_text(delivery_id, "delivery_id")
        normalized_reason = reason.strip() if reason is not None and reason.strip() else None
        normalized_evidence = evidence_ref.strip() if evidence_ref is not None and evidence_ref.strip() else None
        if decision is AcceptanceDecision.CHANGES_REQUESTED and normalized_reason is None:
            raise ValidationError("a rejection reason is required")
        if decision is AcceptanceDecision.ACCEPTED and normalized_evidence is None:
            raise ValidationError("acceptance evidence is required")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.delivery.decide:{order_id}:{delivery_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "decision": decision.value,
                "reason": normalized_reason,
                "evidence_ref": normalized_evidence,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            self._require_owner_manage(actor, order)
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if order.version != expected_version:
                raise VersionConflict()
            delivery = next((item for item in order.deliveries if item.id == delivery_id), None)
            if delivery is None or delivery.status is not DeliveryStatus.SUBMITTED:
                raise InvalidTransition("delivery is unavailable for a decision")
            timestamp = self.now()
            delivery_status = (
                DeliveryStatus.ACCEPTED if decision is AcceptanceDecision.ACCEPTED else DeliveryStatus.RETURNED
            )
            deliveries = tuple(
                replace(item, status=delivery_status, version=item.version + 1, updated_at=timestamp)
                if item.id == delivery_id
                else item
                for item in order.deliveries
            )
            milestone_status = (
                MilestoneStatus.ACCEPTED
                if decision is AcceptanceDecision.ACCEPTED
                else MilestoneStatus.CHANGES_REQUESTED
            )
            milestones = tuple(
                replace(item, status=milestone_status, version=item.version + 1)
                if item.id == delivery.milestone_id
                else item
                for item in order.milestones
            )
            record = AcceptanceRecord(
                id=self.id_factory(),
                milestone_id=delivery.milestone_id,
                delivery_id=delivery.id,
                delivery_revision=delivery.revision,
                decision=decision,
                reason=normalized_reason,
                evidence_ref=normalized_evidence,
                decided_by=actor.actor_id,
                created_at=timestamp,
            )
            settlements = order.settlements
            if decision is AcceptanceDecision.ACCEPTED:
                if any(item.milestone_id == delivery.milestone_id for item in settlements):
                    raise InvalidTransition("the milestone already has a settlement")
                if order.contractor_workspace_id is None:
                    raise InvalidTransition("the order has no contractor")
                settlements = (
                    *settlements,
                    Settlement(
                        id=self.id_factory(),
                        milestone_id=delivery.milestone_id,
                        payee_workspace_id=order.contractor_workspace_id,
                        amount_minor=self._settlement_amount(order, delivery.milestone_id),
                        currency=order.currency,
                        status=SettlementStatus.READY,
                        version=1,
                        accounting_operation_id=None,
                        accounting_receipt_id=None,
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                )
            all_accepted = all(
                item.status in (MilestoneStatus.ACCEPTED, MilestoneStatus.SETTLED) for item in milestones
            )
            updated = replace(
                order,
                deliveries=deliveries,
                milestones=milestones,
                acceptance_records=(*order.acceptance_records, record),
                settlements=settlements,
                status=OrderStatus.ACCEPTED if all_accepted else OrderStatus.IN_DELIVERY,
                version=order.version + 1,
                updated_at=timestamp,
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type=f"commercial.delivery.{decision.value}",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def open_dispute(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        milestone_id: str,
        kind: str,
        reason: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        if not ({"commercial.manage", "admin.commercial.manage"} & actor.permissions):
            raise PermissionDenied()
        milestone_id = self._required_text(milestone_id, "milestone_id")
        kind = self._required_text(kind, "kind")
        reason = self._required_text(reason, "reason")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.dispute.open:{order_id}:{milestone_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "milestone_id": milestone_id,
                "kind": kind,
                "reason": reason,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            if not self._can_manage_participant(actor, order):
                raise OrderNotFound()
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if order.version != expected_version:
                raise VersionConflict()
            if not any(item.id == milestone_id for item in order.milestones):
                raise ValidationError("milestone does not exist")
            if any(item.milestone_id == milestone_id and item.status is DisputeStatus.OPEN for item in order.disputes):
                raise InvalidTransition("an open dispute already exists for the milestone")
            timestamp = self.now()
            dispute = CommercialDispute(
                id=self.id_factory(),
                milestone_id=milestone_id,
                kind=kind,
                reason=reason,
                opened_by=actor.actor_id,
                opened_by_workspace_id=actor.workspace_id,
                status=DisputeStatus.OPEN,
                resolution=None,
                resolved_by=None,
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            updated = replace(
                order,
                disputes=(*order.disputes, dispute),
                version=order.version + 1,
                updated_at=timestamp,
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type="commercial.dispute.opened",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def resolve_dispute(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        dispute_id: str,
        resolution: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        self._require_permission(actor, "admin.commercial.manage")
        dispute_id = self._required_text(dispute_id, "dispute_id")
        resolution = self._required_text(resolution, "resolution")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.dispute.resolve:{order_id}:{dispute_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "dispute_id": dispute_id,
                "resolution": resolution,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None or order.owner_workspace_id not in actor.data_scope_workspace_ids:
                raise OrderNotFound()
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if order.version != expected_version:
                raise VersionConflict()
            dispute = next((item for item in order.disputes if item.id == dispute_id), None)
            if dispute is None or dispute.status is not DisputeStatus.OPEN:
                raise InvalidTransition("dispute is unavailable for resolution")
            timestamp = self.now()
            updated = replace(
                order,
                disputes=tuple(
                    replace(
                        item,
                        status=DisputeStatus.RESOLVED,
                        resolution=resolution,
                        resolved_by=actor.actor_id,
                        version=item.version + 1,
                        updated_at=timestamp,
                    )
                    if item.id == dispute_id
                    else item
                    for item in order.disputes
                ),
                version=order.version + 1,
                updated_at=timestamp,
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type="commercial.dispute.resolved",
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    def freeze_settlement(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        settlement_id: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        return self._change_settlement_state(
            action="freeze",
            actor=actor,
            owner_workspace_id=owner_workspace_id,
            order_id=order_id,
            settlement_id=settlement_id,
            expected_version=expected_version,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def resume_settlement(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        settlement_id: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        return self._change_settlement_state(
            action="resume",
            actor=actor,
            owner_workspace_id=owner_workspace_id,
            order_id=order_id,
            settlement_id=settlement_id,
            expected_version=expected_version,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def settle(
        self,
        *,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        settlement_id: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        return self._change_settlement_state(
            action="pay",
            actor=actor,
            owner_workspace_id=owner_workspace_id,
            order_id=order_id,
            settlement_id=settlement_id,
            expected_version=expected_version,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def _change_settlement_state(
        self,
        *,
        action: AccountingAction,
        actor: Actor,
        owner_workspace_id: str,
        order_id: str,
        settlement_id: str,
        expected_version: int,
        request_id: str,
        idempotency_key: str,
    ) -> CommercialOrder:
        if not ({"commercial.manage", "admin.commercial.manage"} & actor.permissions):
            raise PermissionDenied()
        settlement_id = self._required_text(settlement_id, "settlement_id")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        scope = f"commercial.settlement.{action}:{order_id}:{settlement_id}"
        fingerprint = self._fingerprint(
            {
                "actor_id": actor.actor_id,
                "settlement_id": settlement_id,
                "expected_version": expected_version,
            }
        )
        with self.repository.atomic() as uow:
            order = uow.get_order(owner_workspace_id, order_id)
            if order is None:
                raise OrderNotFound()
            self._require_owner_manage(actor, order)
            replay = self._replay(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return replay
            if order.version != expected_version:
                raise VersionConflict()
            settlement = next((item for item in order.settlements if item.id == settlement_id), None)
            if settlement is None:
                raise ValidationError("settlement does not exist")
            open_disputes = tuple(item for item in order.disputes if item.status is DisputeStatus.OPEN)
            if action == "freeze":
                if settlement.status is not SettlementStatus.READY:
                    raise InvalidTransition("only a ready settlement can be frozen")
                if not any(item.milestone_id == settlement.milestone_id for item in open_disputes):
                    raise InvalidTransition("freeze requires an open dispute for the settlement milestone")
                next_status = SettlementStatus.FROZEN
                accounting_call = self.accounting.freeze_settlement
            elif action == "resume":
                if settlement.status is not SettlementStatus.FROZEN:
                    raise InvalidTransition("only a frozen settlement can be resumed")
                if open_disputes:
                    raise SettlementBlocked()
                next_status = SettlementStatus.READY
                accounting_call = self.accounting.resume_settlement
            elif action == "pay":
                if settlement.status is not SettlementStatus.READY:
                    raise InvalidTransition("only a ready settlement can be paid")
                if open_disputes:
                    raise SettlementBlocked()
                next_status = SettlementStatus.PAID
                accounting_call = self.accounting.pay_settlement
            else:
                raise ValidationError("unsupported settlement action")
            operation_id = self._accounting_operation_id(action, settlement_id, idempotency_key)
            request = AccountingRequest(
                action=action,
                operation_id=operation_id,
                owner_workspace_id=order.owner_workspace_id,
                payee_workspace_id=settlement.payee_workspace_id,
                order_id=order.id,
                milestone_id=settlement.milestone_id,
                settlement_id=settlement.id,
                amount_minor=settlement.amount_minor,
                currency=settlement.currency,
                requested_by=actor.actor_id,
                request_id=self._required_text(request_id, "request_id"),
            )
            receipt = accounting_call(request)
            self._require_accounting_success(receipt, operation_id)
            timestamp = self.now()
            settlements = tuple(
                replace(
                    item,
                    status=next_status,
                    version=item.version + 1,
                    accounting_operation_id=operation_id,
                    accounting_receipt_id=receipt.receipt_id,
                    updated_at=timestamp,
                )
                if item.id == settlement_id
                else item
                for item in order.settlements
            )
            milestones = order.milestones
            order_status = order.status
            if action == "pay":
                milestones = tuple(
                    replace(item, status=MilestoneStatus.SETTLED, version=item.version + 1)
                    if item.id == settlement.milestone_id
                    else item
                    for item in order.milestones
                )
                order_status = (
                    OrderStatus.SETTLED
                    if all(item.status is SettlementStatus.PAID for item in settlements)
                    else order.status
                )
            updated = replace(
                order,
                settlements=settlements,
                milestones=milestones,
                status=order_status,
                version=order.version + 1,
                updated_at=timestamp,
            )
            uow.put_order(updated, expected_version=order.version)
            self._record_command(
                uow,
                workspace_id=owner_workspace_id,
                scope=scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=updated,
            )
            self._audit(
                uow,
                event_type={
                    "freeze": "commercial.settlement.frozen",
                    "resume": "commercial.settlement.resumed",
                    "pay": "commercial.settlement.paid",
                }[action],
                actor=actor,
                workspace_id=owner_workspace_id,
                object_id=order.id,
                request_id=request_id,
                before=order,
                after=updated,
            )
            return updated

    @staticmethod
    def _accounting_operation_id(action: AccountingAction, settlement_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{action}:{settlement_id}:{idempotency_key}".encode()).hexdigest()
        return f"commercial-{action}-{digest[:32]}"

    @staticmethod
    def _require_accounting_success(receipt: AccountingReceipt, operation_id: str) -> None:
        if not receipt.succeeded or receipt.operation_id != operation_id:
            raise AccountingRejected()

    @staticmethod
    def _settlement_amount(order: CommercialOrder, milestone_id: str) -> int:
        contract = next(
            version for version in order.contract_versions if version.id == order.active_contract_version_id
        )
        remaining = contract.amount_minor
        for index, milestone in enumerate(order.milestones):
            amount = (
                remaining
                if index == len(order.milestones) - 1
                else contract.amount_minor * milestone.amount_minor // order.budget_minor
            )
            if milestone.id == milestone_id:
                return amount
            remaining -= amount
        raise ValidationError("milestone does not exist")

    @staticmethod
    def _can_view(actor: Actor, order: CommercialOrder) -> bool:
        participant_workspace_ids = {
            order.owner_workspace_id,
            *(quote.contractor_workspace_id for quote in order.quotes),
        }
        if order.contractor_workspace_id is not None:
            participant_workspace_ids.add(order.contractor_workspace_id)
        member_access = "commercial.view" in actor.permissions and actor.workspace_id in participant_workspace_ids
        admin_access = (
            "admin.commercial.view" in actor.permissions and order.owner_workspace_id in actor.data_scope_workspace_ids
        )
        return member_access or admin_access

    @staticmethod
    def _can_manage_participant(actor: Actor, order: CommercialOrder) -> bool:
        participant_workspace_ids = {
            order.owner_workspace_id,
            *(quote.contractor_workspace_id for quote in order.quotes),
        }
        if order.contractor_workspace_id is not None:
            participant_workspace_ids.add(order.contractor_workspace_id)
        member_access = "commercial.manage" in actor.permissions and actor.workspace_id in participant_workspace_ids
        admin_access = (
            "admin.commercial.manage" in actor.permissions
            and order.owner_workspace_id in actor.data_scope_workspace_ids
        )
        return member_access or admin_access
