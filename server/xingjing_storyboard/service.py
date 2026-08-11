from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .errors import (
    ContractViolation,
    FrozenStoryboardViolation,
    ImportValidationError,
    InvalidAssetReference,
    PermissionDenied,
    QualityGateFailed,
    StoryboardError,
    StoryboardNotFound,
    VersionConflict,
)
from .exchange import ExportFormat, ExportResult, ImportedShot, export_storyboard, import_shots
from .models import (
    AssetReference,
    FrozenStoryboard,
    GenerationStatus,
    Shot,
    ShotReplacement,
    Storyboard,
    StoryboardStatus,
    StoryboardVersion,
    frozen_storyboard_digest,
    storyboard_version_digest,
)
from .ports import (
    AssetReferenceValidator,
    AuditEvent,
    IdentifierFactory,
    StoryboardRepository,
    StoryboardScope,
)
from .quality import QualityEvaluator, QualityPolicy

VIEW_PERMISSION = "shot.view"
MANAGE_PERMISSION = "shot.manage"


@dataclass(frozen=True, slots=True)
class AccessContext:
    tenant_id: str
    workspace_id: str
    project_id: str
    actor_id: str
    request_id: str
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        if any(
            not item.strip()
            for item in (self.tenant_id, self.workspace_id, self.project_id, self.actor_id, self.request_id)
        ):
            raise ContractViolation("ACCESS_CONTEXT_REQUIRED")

    @property
    def scope(self) -> StoryboardScope:
        return StoryboardScope(self.tenant_id, self.workspace_id, self.project_id)


@dataclass(frozen=True, slots=True)
class ShotDraft:
    shot_number: str
    duration_ms: int
    dialogue: str = ""
    shot_size: str | None = None
    camera_movement: str | None = None
    prompt: str = ""
    model_strategy: str | None = None
    cost_tier: str | None = None
    asset_references: tuple[AssetReference, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "shot_number": self.shot_number,
            "duration_ms": self.duration_ms,
            "dialogue": self.dialogue,
            "shot_size": self.shot_size,
            "camera_movement": self.camera_movement,
            "prompt": self.prompt,
            "model_strategy": self.model_strategy,
            "cost_tier": self.cost_tier,
            "asset_references": [item.to_dict() for item in self.asset_references],
        }


@dataclass(frozen=True, slots=True)
class ShotEdit:
    shot_id: str
    shot_number: str | None = None
    duration_ms: int | None = None
    dialogue: str | None = None
    shot_size: str | None = None
    camera_movement: str | None = None
    prompt: str | None = None
    model_strategy: str | None = None
    cost_tier: str | None = None
    asset_references: tuple[AssetReference, ...] | None = None
    negative_prompt: str | None = None
    prompt_template_id: str | None = None
    prompt_variables: dict[str, str] | None = None
    prompt_model_adapter_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "shot_id": self.shot_id,
            "shot_number": self.shot_number,
            "duration_ms": self.duration_ms,
            "dialogue": self.dialogue,
            "shot_size": self.shot_size,
            "camera_movement": self.camera_movement,
            "prompt": self.prompt,
            "model_strategy": self.model_strategy,
            "cost_tier": self.cost_tier,
            "asset_references": (
                None if self.asset_references is None else [item.to_dict() for item in self.asset_references]
            ),
            "negative_prompt": self.negative_prompt,
            "prompt_template_id": self.prompt_template_id,
            "prompt_variables": self.prompt_variables,
            "prompt_model_adapter_version": self.prompt_model_adapter_version,
        }


@dataclass(frozen=True, slots=True)
class MutationResult:
    storyboard: Storyboard
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoryboardPage:
    items: tuple[Storyboard, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class VersionComparison:
    baseline_version: int
    candidate_version: int
    changed_shot_ids: tuple[str, ...]
    fields_by_shot: dict[str, tuple[str, ...]]


class UuidIdentifierFactory:
    def new(self, kind: str) -> str:
        return f"{kind}-{uuid.uuid4()}"


class StoryboardService:
    def __init__(
        self,
        repository: StoryboardRepository,
        asset_validator: AssetReferenceValidator,
        *,
        identifiers: IdentifierFactory | None = None,
        clock: Callable[[], datetime] | None = None,
        quality_policy: QualityPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._asset_validator = asset_validator
        self._identifiers = identifiers or UuidIdentifierFactory()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._quality_policy = quality_policy

    def create_storyboard(
        self,
        context: AccessContext,
        *,
        episode_id: str,
        shots: Sequence[ShotDraft],
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.created"
        self._authorize(context, MANAGE_PERMISSION, action, "pending")
        self._require_idempotency_key(idempotency_key)
        if not episode_id.strip():
            raise ContractViolation("EPISODE_ID_REQUIRED")
        drafts = tuple(shots)
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "episode_id": episode_id,
                "shots": [draft.to_dict() for draft in drafts],
            }
        )
        replay = self._repository.replay(context.scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
        if replay is not None:
            return MutationResult(replay, True)

        storyboard_id = self._identifiers.new("storyboard")
        try:
            now = self._now()
            created_shots = tuple(self._shot_from_draft(draft, position) for position, draft in enumerate(drafts, 1))
            self._validate_assets(context, created_shots)
            version = _version(1, action, context.actor_id, now, created_shots)
            storyboard = Storyboard(
                storyboard_id=storyboard_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                episode_id=episode_id,
                version=1,
                status=StoryboardStatus.DRAFT,
                shots=created_shots,
                versions=(version,),
                created_at=now,
                updated_at=now,
            )
            audit = self._audit(context, action, storyboard_id, "succeeded", {}, _summary(storyboard))
            outcome = self._repository.commit(
                context.scope,
                storyboard,
                expected_version=None,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                audit=audit,
            )
            return MutationResult(outcome.storyboard, outcome.replayed)
        except StoryboardError as error:
            self._record_failure(context, action, storyboard_id, error)
            raise

    def batch_edit(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        edits: Sequence[ShotEdit],
        idempotency_key: str,
    ) -> MutationResult:
        edit_tuple = tuple(edits)
        fingerprint = _fingerprint(
            {
                "operation": "storyboard.batch_edited",
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "edits": [edit.to_dict() for edit in edit_tuple],
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            if not edit_tuple:
                raise ContractViolation("EMPTY_BATCH_EDIT")
            edit_ids = [edit.shot_id for edit in edit_tuple]
            if len(set(edit_ids)) != len(edit_ids):
                raise ContractViolation("DUPLICATE_SHOT_EDIT")
            by_id = {shot.shot_id: shot for shot in storyboard.shots}
            missing = [shot_id for shot_id in edit_ids if shot_id not in by_id]
            if missing:
                raise ContractViolation("SHOT_NOT_FOUND", details={"shot_ids": missing})
            for edit in edit_tuple:
                current = by_id[edit.shot_id]
                by_id[edit.shot_id] = replace(
                    current,
                    shot_number=edit.shot_number if edit.shot_number is not None else current.shot_number,
                    duration_ms=edit.duration_ms if edit.duration_ms is not None else current.duration_ms,
                    dialogue=edit.dialogue if edit.dialogue is not None else current.dialogue,
                    shot_size=edit.shot_size if edit.shot_size is not None else current.shot_size,
                    camera_movement=(
                        edit.camera_movement if edit.camera_movement is not None else current.camera_movement
                    ),
                    prompt=edit.prompt if edit.prompt is not None else current.prompt,
                    model_strategy=edit.model_strategy if edit.model_strategy is not None else current.model_strategy,
                    cost_tier=edit.cost_tier if edit.cost_tier is not None else current.cost_tier,
                    asset_references=(
                        edit.asset_references if edit.asset_references is not None else current.asset_references
                    ),
                    negative_prompt=(
                        edit.negative_prompt if edit.negative_prompt is not None else current.negative_prompt
                    ),
                    prompt_template_id=(
                        edit.prompt_template_id
                        if edit.prompt_template_id is not None
                        else current.prompt_template_id
                    ),
                    prompt_variables=(
                        edit.prompt_variables if edit.prompt_variables is not None else current.prompt_variables
                    ),
                    prompt_model_adapter_version=(
                        edit.prompt_model_adapter_version
                        if edit.prompt_model_adapter_version is not None
                        else current.prompt_model_adapter_version
                    ),
                    quality_issues=(),
                )
            return tuple(by_id[shot.shot_id] for shot in storyboard.shots)

        return self._mutate(
            context,
            action="storyboard.batch_edited",
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def reorder_shots(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        ordered_shot_ids: Sequence[str],
        idempotency_key: str,
    ) -> MutationResult:
        order = tuple(ordered_shot_ids)
        fingerprint = _fingerprint(
            {
                "operation": "storyboard.reordered",
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "ordered_shot_ids": order,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            current_ids = {shot.shot_id for shot in storyboard.shots}
            if len(order) != len(set(order)) or set(order) != current_ids:
                raise ContractViolation("INVALID_SHOT_ORDER")
            by_id = {shot.shot_id: shot for shot in storyboard.shots}
            return tuple(
                replace(by_id[shot_id], position=position, quality_issues=())
                for position, shot_id in enumerate(order, 1)
            )

        return self._mutate(
            context,
            action="storyboard.reordered",
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def split_shot(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        shot_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.shot_split"
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "shot_id": shot_id,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            current = next((item for item in storyboard.shots if item.shot_id == shot_id), None)
            if current is None:
                raise ContractViolation("SHOT_NOT_FOUND", details={"shot_ids": [shot_id]})
            if current.duration_ms < 2:
                raise ContractViolation("SHOT_TOO_SHORT_TO_SPLIT")
            first_duration = current.duration_ms // 2
            second_duration = current.duration_ms - first_duration
            index = current.position - 1
            first = replace(current, shot_number=f"{current.shot_number}A", duration_ms=first_duration, quality_issues=())
            second = replace(
                current,
                shot_id=self._identifiers.new("shot"),
                shot_number=f"{current.shot_number}B",
                duration_ms=second_duration,
                selected_media_id=None,
                generation_status=GenerationStatus.NOT_STARTED,
                quality_issues=(),
                replacements=(),
            )
            combined = (*storyboard.shots[:index], first, second, *storyboard.shots[index + 1 :])
            return tuple(replace(item, position=position) for position, item in enumerate(combined, 1))

        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def merge_shots(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        shot_ids: Sequence[str],
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.shots_merged"
        selected_ids = tuple(shot_ids)
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "shot_ids": selected_ids,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            if len(selected_ids) < 2 or len(selected_ids) != len(set(selected_ids)):
                raise ContractViolation("MERGE_REQUIRES_DISTINCT_SHOTS")
            selected = [item for item in storyboard.shots if item.shot_id in set(selected_ids)]
            if len(selected) != len(selected_ids):
                raise ContractViolation("SHOT_NOT_FOUND", details={"shot_ids": list(selected_ids)})
            selected.sort(key=lambda item: item.position)
            if [item.position for item in selected] != list(range(selected[0].position, selected[-1].position + 1)):
                raise ContractViolation("MERGE_REQUIRES_CONTIGUOUS_SHOTS")
            base = selected[0]
            references = tuple(
                dict.fromkeys(reference for item in selected for reference in item.asset_references)
            )
            merged = replace(
                base,
                duration_ms=sum(item.duration_ms for item in selected),
                dialogue="\n".join(item.dialogue for item in selected if item.dialogue),
                prompt="\n\n".join(item.prompt for item in selected if item.prompt),
                asset_references=references,
                selected_media_id=None,
                generation_status=GenerationStatus.NOT_STARTED,
                quality_issues=(),
                replacements=(),
            )
            selected_set = set(selected_ids)
            combined = tuple(merged if item.shot_id == base.shot_id else item for item in storyboard.shots if item.shot_id not in selected_set or item.shot_id == base.shot_id)
            return tuple(replace(item, position=position) for position, item in enumerate(combined, 1))

        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def restore_shot_version(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        shot_id: str,
        source_version: int,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.shot_version_restored"
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "shot_id": shot_id,
                "source_version": source_version,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            current = next((item for item in storyboard.shots if item.shot_id == shot_id), None)
            historic = next((item for item in storyboard.versions if item.number == source_version), None)
            source = None if historic is None else next((item for item in historic.shots if item.shot_id == shot_id), None)
            if current is None or source is None:
                raise ContractViolation("VERSIONED_SHOT_NOT_FOUND")
            restored = replace(source, shot_id=current.shot_id, position=current.position, quality_issues=())
            return tuple(restored if item.shot_id == shot_id else item for item in storyboard.shots)

        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def select_shot_candidate(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        shot_id: str,
        candidate_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.candidate_selected"
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "shot_id": shot_id,
                "candidate_id": candidate_id,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            if not candidate_id.strip():
                raise ContractViolation("CANDIDATE_ID_REQUIRED")
            result: list[Shot] = []
            for shot in storyboard.shots:
                if shot.shot_id != shot_id:
                    result.append(shot)
                    continue
                available = {item.new_media_id for item in shot.replacements}
                if shot.selected_media_id:
                    available.add(shot.selected_media_id)
                if candidate_id not in available:
                    raise ContractViolation("CANDIDATE_NOT_FOUND")
                if candidate_id == shot.selected_media_id:
                    raise ContractViolation("CANDIDATE_ALREADY_SELECTED")
                result.append(
                    replace(
                        shot,
                        selected_media_id=candidate_id,
                        generation_status=GenerationStatus.SUCCEEDED,
                        quality_issues=(),
                        replacements=(
                            *shot.replacements,
                            ShotReplacement(
                                replacement_id=self._identifiers.new("replacement"),
                                previous_media_id=shot.selected_media_id,
                                new_media_id=candidate_id,
                                reason="candidate_selected",
                                actor_id=context.actor_id,
                                replaced_at=self._now(),
                            ),
                        ),
                    )
                )
            if not any(item.shot_id == shot_id for item in storyboard.shots):
                raise ContractViolation("SHOT_NOT_FOUND", details={"shot_ids": [shot_id]})
            return tuple(result)

        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def run_quality_check(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.quality_checked"
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "policy": self._quality_policy_payload(),
            }
        )
        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=lambda storyboard: self._quality_evaluator().evaluate(storyboard.shots),
        )

    def replace_shot_media(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        shot_id: str,
        new_media_id: str,
        reason: str,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.shot_replaced"
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "shot_id": shot_id,
                "new_media_id": new_media_id,
                "reason": reason,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            if not new_media_id.strip() or not reason.strip():
                raise ContractViolation("REPLACEMENT_INPUT_REQUIRED")
            shots: list[Shot] = []
            found = False
            now = self._now()
            for shot in storyboard.shots:
                if shot.shot_id != shot_id:
                    shots.append(shot)
                    continue
                found = True
                replacement = ShotReplacement(
                    replacement_id=self._identifiers.new("replacement"),
                    previous_media_id=shot.selected_media_id,
                    new_media_id=new_media_id,
                    reason=reason,
                    actor_id=context.actor_id,
                    replaced_at=now,
                )
                shots.append(
                    replace(
                        shot,
                        selected_media_id=new_media_id,
                        generation_status=GenerationStatus.SUCCEEDED,
                        replacements=(*shot.replacements, replacement),
                    )
                )
            if not found:
                raise ContractViolation("SHOT_NOT_FOUND", details={"shot_ids": [shot_id]})
            return tuple(shots)

        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def record_shot_generation_task(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        shot_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.shot_generation_requested"
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "shot_id": shot_id,
                "task_id": task_id,
            }
        )

        def transform(storyboard: Storyboard) -> tuple[Shot, ...]:
            if not task_id.strip():
                raise ContractViolation("GENERATION_TASK_ID_REQUIRED")
            found = False
            result: list[Shot] = []
            for shot in storyboard.shots:
                if shot.shot_id == shot_id:
                    found = True
                    result.append(
                        replace(
                            shot,
                            generation_task_id=task_id,
                            generation_status=GenerationStatus.QUEUED,
                        )
                    )
                else:
                    result.append(shot)
            if not found:
                raise ContractViolation("SHOT_NOT_FOUND", details={"shot_ids": [shot_id]})
            return tuple(result)

        return self._mutate(
            context,
            action=action,
            storyboard_id=storyboard_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            transform=transform,
        )

    def freeze_storyboard(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.frozen"
        self._authorize(context, MANAGE_PERMISSION, action, storyboard_id)
        self._require_idempotency_key(idempotency_key)
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "expected_version": expected_version,
                "policy": self._quality_policy_payload(),
            }
        )
        replay = self._repository.replay(context.scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
        if replay is not None:
            return MutationResult(replay, True)
        try:
            current = self._owned_storyboard(context, storyboard_id)
            self._assert_mutable(current)
            if current.version != expected_version:
                raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version})
            checked_shots = self._quality_evaluator().evaluate(current.shots)
            self._validate_assets(context, checked_shots)
            blockers = [
                issue.issue_id
                for shot in checked_shots
                for issue in shot.quality_issues
                if issue.severity.value == "error"
            ]
            if blockers:
                raise QualityGateFailed("QUALITY_GATE_FAILED", details={"issue_ids": blockers})
            now = self._now()
            next_version = current.version + 1
            draft_snapshot = FrozenStoryboard(
                snapshot_id=self._identifiers.new("snapshot"),
                storyboard_id=current.storyboard_id,
                tenant_id=current.tenant_id,
                workspace_id=current.workspace_id,
                project_id=current.project_id,
                episode_id=current.episode_id,
                source_version=next_version,
                shots=checked_shots,
                frozen_at=now,
                digest="",
            )
            snapshot = replace(draft_snapshot, digest=frozen_storyboard_digest(draft_snapshot))
            frozen = replace(
                current,
                version=next_version,
                status=StoryboardStatus.FROZEN,
                shots=checked_shots,
                versions=(*current.versions, _version(next_version, action, context.actor_id, now, checked_shots)),
                updated_at=now,
                frozen_snapshot=snapshot,
            )
            audit = self._audit(
                context,
                action,
                storyboard_id,
                "succeeded",
                _summary(current),
                _summary(frozen),
            )
            outcome = self._repository.commit(
                context.scope,
                frozen,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                audit=audit,
            )
            return MutationResult(outcome.storyboard, outcome.replayed)
        except StoryboardError as error:
            self._record_failure(context, action, storyboard_id, error)
            raise

    def export_storyboard(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        format: ExportFormat,
        idempotency_key: str,
    ) -> ExportResult:
        action = "storyboard.exported"
        self._authorize(context, MANAGE_PERMISSION, action, storyboard_id)
        self._require_idempotency_key(idempotency_key)
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "storyboard_id": storyboard_id,
                "format": format.value,
            }
        )
        replay = self._repository.replay_export(context.scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
        if replay is not None:
            return ExportResult.from_storage(replay, replayed=True)
        try:
            storyboard = self._owned_storyboard(context, storyboard_id)
            result = export_storyboard(storyboard, format)
            stored, replayed = self._repository.commit_export(
                context.scope,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                result=result.to_storage(),
                audit=self._audit(
                    context,
                    action,
                    storyboard_id,
                    "succeeded",
                    _summary(storyboard),
                    {"format": format.value, "sha256": result.sha256, "version": storyboard.version},
                ),
            )
            return ExportResult.from_storage(stored, replayed=replayed)
        except StoryboardError as error:
            self._record_failure(context, action, storyboard_id, error)
            raise

    def import_storyboard(
        self,
        context: AccessContext,
        *,
        episode_id: str,
        filename: str,
        content: bytes,
        idempotency_key: str,
    ) -> MutationResult:
        action = "storyboard.imported"
        storyboard_id = "pending"
        self._authorize(context, MANAGE_PERMISSION, action, storyboard_id)
        self._require_idempotency_key(idempotency_key)
        if not episode_id.strip() or not filename.strip():
            raise ImportValidationError("INVALID_IMPORT")
        fingerprint = _fingerprint(
            {
                "operation": action,
                "scope": _scope_dict(context.scope),
                "episode_id": episode_id,
                "filename": filename,
                "content_sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        replay = self._repository.replay(context.scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
        if replay is not None:
            return MutationResult(replay, True)
        try:
            imported = import_shots(filename, content)
            storyboard_id = self._identifiers.new("storyboard")
            now = self._now()
            shots = tuple(self._shot_from_imported(item) for item in imported)
            self._validate_assets(context, shots)
            storyboard = Storyboard(
                storyboard_id=storyboard_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=context.project_id,
                episode_id=episode_id,
                version=1,
                status=StoryboardStatus.DRAFT,
                shots=shots,
                versions=(_version(1, action, context.actor_id, now, shots),),
                created_at=now,
                updated_at=now,
            )
            outcome = self._repository.commit(
                context.scope,
                storyboard,
                expected_version=None,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                audit=self._audit(context, action, storyboard_id, "succeeded", {}, _summary(storyboard)),
            )
            return MutationResult(outcome.storyboard, outcome.replayed)
        except StoryboardError as error:
            self._record_failure(context, action, storyboard_id, error)
            raise

    def compare_versions(
        self,
        context: AccessContext,
        *,
        storyboard_id: str,
        baseline_version: int,
        candidate_version: int,
    ) -> VersionComparison:
        self._authorize(context, VIEW_PERMISSION, "storyboard.versions_compared", storyboard_id)
        storyboard = self._owned_storyboard(context, storyboard_id)
        baseline = next((item for item in storyboard.versions if item.number == baseline_version), None)
        candidate = next((item for item in storyboard.versions if item.number == candidate_version), None)
        if baseline is None or candidate is None:
            raise ContractViolation("VERSION_NOT_FOUND")
        baseline_shots = {shot.shot_id: shot.to_dict() for shot in baseline.shots}
        candidate_shots = {shot.shot_id: shot.to_dict() for shot in candidate.shots}
        changes: dict[str, tuple[str, ...]] = {}
        for shot_id in sorted(set(baseline_shots) | set(candidate_shots)):
            before = baseline_shots.get(shot_id)
            after = candidate_shots.get(shot_id)
            if before is None:
                changes[shot_id] = ("__added__",)
            elif after is None:
                changes[shot_id] = ("__removed__",)
            else:
                fields = tuple(
                    key
                    for key in sorted(set(before) | set(after))
                    if key != "shot_id" and before.get(key) != after.get(key)
                )
                if fields:
                    changes[shot_id] = fields
        return VersionComparison(
            baseline_version=baseline_version,
            candidate_version=candidate_version,
            changed_shot_ids=tuple(changes),
            fields_by_shot=changes,
        )

    def get_storyboard(self, context: AccessContext, storyboard_id: str) -> Storyboard:
        self._authorize(context, VIEW_PERMISSION, "storyboard.viewed", storyboard_id, audit_denial=True)
        storyboard = self._repository.get(context.scope, storyboard_id)
        if storyboard is None:
            raise StoryboardNotFound("STORYBOARD_NOT_FOUND")
        return storyboard

    def list_storyboards(self, context: AccessContext, *, offset: int = 0, limit: int = 50) -> StoryboardPage:
        self._authorize(context, VIEW_PERMISSION, "storyboard.listed", "collection", audit_denial=True)
        if offset < 0 or not 1 <= limit <= 200:
            raise ContractViolation("INVALID_PAGINATION")
        items = sorted(
            self._repository.list(context.scope),
            key=lambda item: (item.episode_id, item.storyboard_id),
        )
        return StoryboardPage(tuple(items[offset : offset + limit]), len(items), offset, limit)

    def list_audit(self, context: AccessContext, *, storyboard_id: str | None = None) -> tuple[AuditEvent, ...]:
        self._authorize(context, VIEW_PERMISSION, "storyboard.audit_viewed", storyboard_id or "collection")
        return self._repository.list_audit(context.scope, storyboard_id=storyboard_id)

    def _mutate(
        self,
        context: AccessContext,
        *,
        action: str,
        storyboard_id: str,
        expected_version: int,
        idempotency_key: str,
        fingerprint: str,
        transform: Callable[[Storyboard], tuple[Shot, ...]],
    ) -> MutationResult:
        self._authorize(context, MANAGE_PERMISSION, action, storyboard_id)
        self._require_idempotency_key(idempotency_key)
        replay = self._repository.replay(context.scope, idempotency_key=idempotency_key, fingerprint=fingerprint)
        if replay is not None:
            return MutationResult(replay, True)
        try:
            current = self._owned_storyboard(context, storyboard_id)
            self._assert_mutable(current)
            if current.version != expected_version:
                raise VersionConflict("VERSION_CONFLICT", details={"current_version": current.version})
            shots = transform(current)
            self._validate_assets(context, shots)
            now = self._now()
            next_version = current.version + 1
            changed = replace(
                current,
                version=next_version,
                shots=shots,
                versions=(*current.versions, _version(next_version, action, context.actor_id, now, shots)),
                updated_at=now,
            )
            audit = self._audit(
                context,
                action,
                storyboard_id,
                "succeeded",
                _summary(current),
                _summary(changed),
            )
            outcome = self._repository.commit(
                context.scope,
                changed,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                audit=audit,
            )
            return MutationResult(outcome.storyboard, outcome.replayed)
        except StoryboardError as error:
            self._record_failure(context, action, storyboard_id, error)
            raise

    def _shot_from_draft(self, draft: ShotDraft, position: int) -> Shot:
        return Shot(
            shot_id=self._identifiers.new("shot"),
            position=position,
            shot_number=draft.shot_number,
            shot_size=draft.shot_size,
            camera_movement=draft.camera_movement,
            dialogue=draft.dialogue,
            duration_ms=draft.duration_ms,
            prompt=draft.prompt,
            model_strategy=draft.model_strategy,
            cost_tier=draft.cost_tier,
            asset_references=draft.asset_references,
            selected_media_id=None,
            generation_status=GenerationStatus.NOT_STARTED,
        )

    def _shot_from_imported(self, imported: ImportedShot) -> Shot:
        return Shot(
            shot_id=imported.shot_id or self._identifiers.new("shot"),
            position=imported.position,
            shot_number=imported.shot_number,
            shot_size=imported.shot_size,
            camera_movement=imported.camera_movement,
            dialogue=imported.dialogue,
            duration_ms=imported.duration_ms,
            prompt=imported.prompt,
            model_strategy=imported.model_strategy,
            cost_tier=imported.cost_tier,
            asset_references=imported.asset_references,
            selected_media_id=imported.selected_media_id,
            generation_status=imported.generation_status,
        )

    def _validate_assets(self, context: AccessContext, shots: Sequence[Shot]) -> None:
        references = tuple(dict.fromkeys(reference for shot in shots for reference in shot.asset_references))
        failures = self._asset_validator.validate(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            references=references,
        )
        if failures:
            raise InvalidAssetReference(
                "INVALID_ASSET_REFERENCE",
                details={
                    "failures": [
                        {
                            "asset_id": failure.asset_id,
                            "asset_version_id": failure.asset_version_id,
                            "code": failure.code,
                        }
                        for failure in failures
                    ]
                },
            )

    def _owned_storyboard(self, context: AccessContext, storyboard_id: str) -> Storyboard:
        storyboard = self._repository.get(context.scope, storyboard_id)
        if storyboard is None:
            raise StoryboardNotFound("STORYBOARD_NOT_FOUND")
        return storyboard

    @staticmethod
    def _assert_mutable(storyboard: Storyboard) -> None:
        if storyboard.status is StoryboardStatus.FROZEN:
            raise FrozenStoryboardViolation("STORYBOARD_FROZEN")

    def _authorize(
        self,
        context: AccessContext,
        permission: str,
        action: str,
        object_id: str,
        *,
        audit_denial: bool = True,
    ) -> None:
        if permission in context.permissions:
            return
        error = PermissionDenied("PERMISSION_DENIED", details={"permission": permission})
        if audit_denial:
            self._record_failure(context, action, object_id, error)
        raise error

    @staticmethod
    def _require_idempotency_key(value: str) -> None:
        if not value.strip():
            raise ContractViolation("IDEMPOTENCY_KEY_REQUIRED")

    def _record_failure(self, context: AccessContext, action: str, object_id: str, error: StoryboardError) -> None:
        self._repository.append_audit(self._audit(context, action, object_id, "failed", {}, {}, error_code=error.code))

    def _audit(
        self,
        context: AccessContext,
        action: str,
        object_id: str,
        result: str,
        before: dict[str, object],
        after: dict[str, object],
        *,
        error_code: str | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            request_id=context.request_id,
            actor_id=context.actor_id,
            action=action,
            object_id=object_id,
            result=result,
            occurred_at=self._now(),
            before=before,
            after=after,
            error_code=error_code,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContractViolation("TIMEZONE_REQUIRED")
        return value

    def _quality_evaluator(self) -> QualityEvaluator:
        if self._quality_policy is None:
            raise ContractViolation("QUALITY_POLICY_REQUIRED")
        return QualityEvaluator(self._quality_policy)

    def _quality_policy_payload(self) -> dict[str, object]:
        policy = self._quality_policy
        if policy is None:
            return {"configured": False}
        return {
            "minimum_duration_ms": policy.minimum_duration_ms,
            "maximum_duration_ms": policy.maximum_duration_ms,
            "max_adjacent_duration_delta_ms": policy.max_adjacent_duration_delta_ms,
            "max_dialogue_characters_per_second": policy.max_dialogue_characters_per_second,
            "require_scene_reference": policy.require_scene_reference,
            "require_character_for_dialogue": policy.require_character_for_dialogue,
            "compliance_terms": sorted(policy.compliance_terms),
        }


def _version(
    number: int, action: str, actor_id: str, occurred_at: datetime, shots: tuple[Shot, ...]
) -> StoryboardVersion:
    return StoryboardVersion(
        number=number,
        action=action,
        actor_id=actor_id,
        occurred_at=occurred_at,
        shots=shots,
        digest=storyboard_version_digest(shots),
    )


def _summary(storyboard: Storyboard) -> dict[str, object]:
    return {
        "version": storyboard.version,
        "status": storyboard.status.value,
        "shot_ids": [shot.shot_id for shot in storyboard.shots],
        "version_digest": storyboard.versions[-1].digest,
    }


def _scope_dict(scope: StoryboardScope) -> dict[str, str]:
    return {
        "tenant_id": scope.tenant_id,
        "workspace_id": scope.workspace_id,
        "project_id": scope.project_id,
    }


def _fingerprint(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()
