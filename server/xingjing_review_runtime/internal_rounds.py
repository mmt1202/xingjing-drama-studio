"""Staff-only reviews of immutable rendered video versions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.xingjing_editing_persistence.models import FinalVideoVersionRow, TimelineVersionRow
from server.xingjing_platform_persistence.persistence import OutboxRow, ProjectRow
from server.xingjing_review.errors import InvalidTransition, LinkUnavailable, VersionConflict
from server.xingjing_review_persistence.internal_models import (
    InternalReviewCommandRow,
    InternalReviewCommentRow,
    InternalReviewRoundRow,
)
from server.xingjing_review_persistence.models import ReviewAuditRow

if TYPE_CHECKING:
    from server.xingjing_review_runtime.runtime import ReviewMedia, ReviewRuntime, StaffReviewContext


class CreateRound(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    finalVideoVersionId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=4000)


class RoundVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: StrictInt = Field(ge=1)


class AddComment(RoundVersion):
    timecodeMs: StrictInt = Field(ge=0)
    shotId: str | None = Field(default=None, min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=4000)
    severity: Literal["note", "minor", "major", "critical"]
    parentCommentId: str | None = Field(default=None, min_length=1, max_length=128)


class ChangeComment(RoundVersion):
    commentVersion: StrictInt = Field(ge=1)
    status: Literal["open", "in_progress", "resolved", "reopened"]


class DecideRound(RoundVersion):
    decision: Literal["approved", "changes_requested"]
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_note(self) -> DecideRound:
        if self.decision == "changes_requested" and not self.note:
            raise ValueError("INTERNAL_REVIEW_DECISION_NOTE_REQUIRED")
        return self


class LockRound(RoundVersion):
    note: str = Field(default="", max_length=4000)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def _scope(model, context: StaffReviewContext):
    return (model.tenant_id == context.scope.tenant_id, model.workspace_id == context.scope.workspace_id)


class InternalReviewOperations:
    def __init__(self, runtime: ReviewRuntime) -> None:
        self.runtime = runtime

    def list(
        self,
        context: StaffReviewContext,
        project_id: str,
        q: str,
        state: str,
        offset: int,
        limit: int,
        final_version_id: str | None = None,
        episode_id: str | None = None,
    ) -> dict[str, object]:
        if state and state not in {"collecting", "approved", "changes_requested", "locked"}:
            raise ValueError("INTERNAL_REVIEW_STATE_INVALID")
        with self.runtime._session_factory() as session:
            conditions = [*_scope(InternalReviewRoundRow, context), InternalReviewRoundRow.project_id == project_id]
            final_conditions = [*_scope(FinalVideoVersionRow, context), FinalVideoVersionRow.project_id == project_id]
            if final_version_id:
                conditions.append(InternalReviewRoundRow.final_video_version_id == final_version_id)
            if episode_id:
                versions = select(TimelineVersionRow.version_id).where(
                    *_scope(TimelineVersionRow, context),
                    TimelineVersionRow.project_id == project_id,
                    TimelineVersionRow.snapshot["episode_id"].as_string() == episode_id,
                )
                final_conditions.append(FinalVideoVersionRow.timeline_version_id.in_(versions))
                eligible = select(FinalVideoVersionRow.version_id).where(*final_conditions)
                conditions.append(InternalReviewRoundRow.final_video_version_id.in_(eligible))
            if q.strip():
                literal = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                conditions.append(
                    or_(
                        InternalReviewRoundRow.title.ilike(f"%{literal}%", escape="\\"),
                        InternalReviewRoundRow.id.ilike(f"%{literal}%", escape="\\"),
                        InternalReviewRoundRow.final_video_version_id.ilike(f"%{literal}%", escape="\\"),
                        InternalReviewRoundRow.note.ilike(f"%{literal}%", escape="\\"),
                        InternalReviewRoundRow.decision_note.ilike(f"%{literal}%", escape="\\"),
                    )
                )
            if state == "locked":
                conditions.append(InternalReviewRoundRow.locked.is_(True))
            elif state:
                conditions.append(InternalReviewRoundRow.state == state)
            total = session.scalar(select(func.count()).select_from(InternalReviewRoundRow).where(*conditions)) or 0
            rows = session.scalars(
                select(InternalReviewRoundRow)
                .where(*conditions)
                .order_by(InternalReviewRoundRow.number.desc(), InternalReviewRoundRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            finals = session.scalars(
                select(FinalVideoVersionRow)
                .where(*final_conditions)
                .order_by(FinalVideoVersionRow.created_at.desc(), FinalVideoVersionRow.version_id)
            ).all()
            return {
                "rounds": [self._round(session, context, row) for row in rows],
                "total": total,
                "finalVideos": [self._final(session, context, row) for row in finals],
                **self._permissions(context),
                "workspaceId": context.scope.workspace_id,
                "projectId": project_id,
                "actorId": context.trusted.actor_id,
            }

    def detail(self, context: StaffReviewContext, project_id: str, round_id: str) -> dict[str, object]:
        with self.runtime._session_factory() as session:
            row = self._get_round(session, context, project_id, round_id)
            final = self._get_final(session, context, project_id, row.final_video_version_id)
            media: dict[str, object] = {"available": False, "url": None, "expiresAt": None}
            # A lost render object should not hide the review history or block issue handling.
            from server.xingjing_review_runtime.runtime import ReviewRuntimeConfigurationError

            try:
                verified = self._verified_media(context.scope, project_id, final.version_id)
                expires = datetime.now(UTC) + timedelta(seconds=120)
                ticket = self._ticket(context, row, expires)
                media = {
                    "available": True,
                    "url": f"/api/v1/internal-review-media/{row.id}?ticket={ticket}",
                    "expiresAt": _iso(expires),
                    "mediaType": verified.media_type,
                    "filename": verified.filename,
                }
            except ReviewRuntimeConfigurationError:
                media["reasonCode"] = "INTERNAL_REVIEW_MEDIA_UNAVAILABLE"
            return {
                "round": self._round(session, context, row),
                "finalVideo": self._final(session, context, final),
                "comments": [self._comment(item) for item in self._comments(session, context, row.id)],
                "media": media,
                **self._permissions(context),
                "workspaceId": context.scope.workspace_id,
                "projectId": project_id,
                "actorId": context.trusted.actor_id,
            }

    def command(
        self,
        context: StaffReviewContext,
        project_id: str,
        action: str,
        data: Mapping[str, object],
        key: str,
        round_id: str | None = None,
        comment_id: str | None = None,
    ) -> dict[str, object]:
        models: dict[str, type[BaseModel]] = {
            "create": CreateRound,
            "comment": AddComment,
            "status": ChangeComment,
            "decision": DecideRound,
            "lock": LockRound,
            "reopen": LockRound,
            "handoff": RoundVersion,
        }
        if action not in models or not key or len(key) > 128:
            raise ValueError("INTERNAL_REVIEW_COMMAND_INVALID")
        try:
            payload = models[action].model_validate(data).model_dump(mode="json")
        except PydanticValidationError as error:
            raise ValueError("INTERNAL_REVIEW_REQUEST_INVALID") from error
        fingerprint = hashlib.sha256(
            json.dumps(
                [project_id, round_id, comment_id, action, payload],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        try:
            with self.runtime._session_factory() as session, session.begin():
                # Serialize every mutation within a project, including first-round creation on SQLite.
                session.execute(
                    update(ProjectRow)
                    .where(*_scope(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None))
                    .values(updated_at=ProjectRow.updated_at)
                )
                project = session.scalar(
                    select(ProjectRow)
                    .where(*_scope(ProjectRow, context), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None))
                    .with_for_update()
                )
                if project is None:
                    raise LinkUnavailable()
                receipt = session.get(
                    InternalReviewCommandRow,
                    (context.scope.tenant_id, context.scope.workspace_id, context.trusted.actor_id, key),
                )
                if receipt is not None:
                    if receipt.fingerprint != fingerprint:
                        raise VersionConflict("INTERNAL_REVIEW_IDEMPOTENCY_CONFLICT")
                    return receipt.response
                now = datetime.now(UTC)
                if action == "create":
                    row = self._create(
                        session,
                        context,
                        project_id,
                        cast(str, payload["finalVideoVersionId"]),
                        cast(str, payload["title"]),
                        cast(str, payload["note"]),
                        now,
                    )
                    before = None
                    result: dict[str, object] = {"round": self._round(session, context, row)}
                else:
                    row = self._get_round(session, context, project_id, round_id or "")
                    if row.version != payload["version"]:
                        raise VersionConflict("INTERNAL_REVIEW_VERSION_CONFLICT")
                    if row.locked and action != "handoff":
                        raise InvalidTransition("INTERNAL_REVIEW_LOCKED")
                    before = self._round(session, context, row)
                    if action == "status":
                        before["comment"] = self._comment(self._get_comment(session, context, row.id, comment_id or ""))
                    result = self._mutate(session, context, row, action, payload, comment_id, now)
                    if action != "handoff":
                        row.version += 1
                        row.updated_at = now
                    session.flush()
                    result["round"] = self._round(session, context, row)
                after = self._round(session, context, row)
                self._audit(session, context, row, action, before, after, result, now)
                session.add(
                    InternalReviewCommandRow(
                        tenant_id=context.scope.tenant_id,
                        workspace_id=context.scope.workspace_id,
                        actor_id=context.trusted.actor_id,
                        idempotency_key=key,
                        fingerprint=fingerprint,
                        response=result,
                        created_at=now,
                    )
                )
                session.flush()
                return result
        except IntegrityError as error:
            raise VersionConflict("INTERNAL_REVIEW_CONCURRENT_COMMAND") from error

    def _create(
        self,
        session: Session,
        context: StaffReviewContext,
        project_id: str,
        version_id: str,
        title: str,
        note: str,
        now: datetime,
    ) -> InternalReviewRoundRow:
        final = self._get_final(session, context, project_id, version_id)
        self._verified_media(context.scope, project_id, version_id)
        episode_id = self._final(session, context, final)["episodeId"]
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError("INTERNAL_REVIEW_EPISODE_REQUIRED")
        timeline_versions = select(TimelineVersionRow.version_id).where(
            *_scope(TimelineVersionRow, context),
            TimelineVersionRow.project_id == project_id,
            TimelineVersionRow.snapshot["episode_id"].as_string() == episode_id,
        )
        final_versions = select(FinalVideoVersionRow.version_id).where(
            *_scope(FinalVideoVersionRow, context),
            FinalVideoVersionRow.project_id == project_id,
            FinalVideoVersionRow.timeline_version_id.in_(timeline_versions),
        )
        latest = session.scalar(
            select(InternalReviewRoundRow)
            .where(
                *_scope(InternalReviewRoundRow, context),
                InternalReviewRoundRow.project_id == project_id,
                InternalReviewRoundRow.final_video_version_id.in_(final_versions),
            )
            .order_by(InternalReviewRoundRow.number.desc())
            .limit(1)
        )
        if latest is not None and not latest.locked:
            raise InvalidTransition("INTERNAL_REVIEW_PREVIOUS_ROUND_OPEN")
        maximum = (
            session.scalar(
                select(func.max(InternalReviewRoundRow.number)).where(
                    *_scope(InternalReviewRoundRow, context), InternalReviewRoundRow.project_id == project_id
                )
            )
            or 0
        )
        row = InternalReviewRoundRow(
            tenant_id=context.scope.tenant_id,
            workspace_id=context.scope.workspace_id,
            id=str(uuid4()),
            project_id=project_id,
            final_video_version_id=version_id,
            number=maximum + 1,
            title=title,
            note=note,
            state="collecting",
            locked=False,
            version=1,
            created_by=context.trusted.actor_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def _mutate(
        self,
        session: Session,
        context: StaffReviewContext,
        row: InternalReviewRoundRow,
        action: str,
        data: dict[str, object],
        comment_id: str | None,
        now: datetime,
    ) -> dict[str, object]:
        if action == "comment":
            final = self._final(
                session, context, self._get_final(session, context, row.project_id, row.final_video_version_id)
            )
            timecode = cast(int, data["timecodeMs"])
            duration = final["durationMs"]
            if not isinstance(duration, int) or duration <= 0 or timecode >= duration:
                raise ValueError("INTERNAL_REVIEW_TIMECODE_INVALID")
            parent_id = cast(str | None, data["parentCommentId"])
            shot_id = cast(str | None, data["shotId"])
            if parent_id:
                parent = self._get_comment(session, context, row.id, parent_id)
                if parent.parent_comment_id is not None:
                    raise ValueError("INTERNAL_REVIEW_REPLY_PARENT_INVALID")
                timecode, shot_id = parent.timecode_ms, parent.shot_id
            elif shot_id is not None:
                shots = cast(list[dict[str, object]], final["shots"])
                if not any(
                    shot["id"] == shot_id and cast(int, shot["startMs"]) <= timecode < cast(int, shot["endMs"])
                    for shot in shots
                ):
                    raise ValueError("INTERNAL_REVIEW_SHOT_TIMECODE_INVALID")
            comment = InternalReviewCommentRow(
                tenant_id=context.scope.tenant_id,
                workspace_id=context.scope.workspace_id,
                id=str(uuid4()),
                round_id=row.id,
                final_video_version_id=row.final_video_version_id,
                author_id=context.trusted.actor_id,
                timecode_ms=timecode,
                shot_id=shot_id,
                body=cast(str, data["body"]),
                severity=cast(str, data["severity"]),
                parent_comment_id=parent_id,
                status="open",
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(comment)
            if parent_id is None:
                self._reset_decision(row)
            session.flush()
            return {"comment": self._comment(comment)}
        if action == "status":
            comment = self._get_comment(session, context, row.id, comment_id or "")
            if comment.parent_comment_id is not None:
                raise ValueError("INTERNAL_REVIEW_REPLY_HAS_NO_STATUS")
            if comment.version != data["commentVersion"]:
                raise VersionConflict("INTERNAL_REVIEW_COMMENT_VERSION_CONFLICT")
            transitions = {
                "open": {"in_progress", "resolved"},
                "in_progress": {"resolved", "open"},
                "resolved": {"reopened"},
                "reopened": {"in_progress", "resolved"},
            }
            target = cast(str, data["status"])
            if target not in transitions[comment.status]:
                raise InvalidTransition("INTERNAL_REVIEW_COMMENT_TRANSITION_INVALID")
            comment.status, comment.version, comment.updated_at = target, comment.version + 1, now
            self._reset_decision(row)
            return {"comment": self._comment(comment)}
        if action == "decision":
            if data["decision"] == "approved" and any(
                item.parent_comment_id is None and item.status != "resolved"
                for item in self._comments(session, context, row.id)
            ):
                raise InvalidTransition("INTERNAL_REVIEW_OPEN_ISSUES")
            row.state, row.decision_note, row.decided_by, row.decided_at = (
                cast(str, data["decision"]),
                cast(str, data["note"]),
                context.trusted.actor_id,
                now,
            )
            return {}
        if action == "lock":
            if row.state == "collecting":
                raise InvalidTransition("INTERNAL_REVIEW_DECISION_REQUIRED")
            row.locked, row.locked_at = True, now
            return {"lockNote": data["note"]}
        if action == "reopen":
            if row.state == "collecting":
                raise InvalidTransition("INTERNAL_REVIEW_DECISION_REQUIRED")
            self._reset_decision(row)
            return {"reopenNote": data["note"]}
        if action == "handoff":
            if row.state != "approved" or not row.locked:
                raise InvalidTransition("INTERNAL_REVIEW_APPROVED_LOCK_REQUIRED")
            delivery_target = session.scalar(
                select(ProjectRow.target_platform).where(
                    *_scope(ProjectRow, context), ProjectRow.id == row.project_id, ProjectRow.deleted_at.is_(None)
                )
            )
            if not isinstance(delivery_target, str) or delivery_target.strip() not in {
                "douyin",
                "kuaishou",
                "wechat",
                "bilibili",
                "web",
            }:
                raise ValueError("INTERNAL_REVIEW_DELIVERY_TARGET_INVALID")
            final = self._final(
                session, context, self._get_final(session, context, row.project_id, row.final_video_version_id)
            )
            return {
                "navigation": {
                    "target": "release-package",
                    "deliveryTarget": delivery_target.strip(),
                    "projectVersion": row.final_video_version_id,
                    "timelineId": final["timelineId"],
                    "episodeId": final["episodeId"],
                }
            }
        raise ValueError("INTERNAL_REVIEW_COMMAND_INVALID")

    @staticmethod
    def _reset_decision(row: InternalReviewRoundRow) -> None:
        row.state, row.decision_note, row.decided_by, row.decided_at = "collecting", None, None, None

    @staticmethod
    def _permissions(context: StaffReviewContext) -> dict[str, object]:
        manage = "review.manage" in context.trusted.permissions
        return {
            "canManage": manage,
            "canComment": manage,
            "canDecide": manage,
            "canLock": manage,
            "canHandoff": "export.view" in context.trusted.permissions,
        }

    @staticmethod
    def _get_round(
        session: Session, context: StaffReviewContext, project_id: str, round_id: str
    ) -> InternalReviewRoundRow:
        row = session.scalar(
            select(InternalReviewRoundRow)
            .where(
                *_scope(InternalReviewRoundRow, context),
                InternalReviewRoundRow.project_id == project_id,
                InternalReviewRoundRow.id == round_id,
            )
            .with_for_update()
        )
        if row is None:
            raise LinkUnavailable()
        return row

    @staticmethod
    def _get_final(
        session: Session, context: StaffReviewContext, project_id: str, version_id: str
    ) -> FinalVideoVersionRow:
        row = session.scalar(
            select(FinalVideoVersionRow).where(
                *_scope(FinalVideoVersionRow, context),
                FinalVideoVersionRow.project_id == project_id,
                FinalVideoVersionRow.version_id == version_id,
            )
        )
        if row is None:
            raise LinkUnavailable()
        return row

    @staticmethod
    def _get_comment(
        session: Session, context: StaffReviewContext, round_id: str, comment_id: str
    ) -> InternalReviewCommentRow:
        row = session.scalar(
            select(InternalReviewCommentRow).where(
                *_scope(InternalReviewCommentRow, context),
                InternalReviewCommentRow.round_id == round_id,
                InternalReviewCommentRow.id == comment_id,
            )
        )
        if row is None:
            raise LinkUnavailable()
        return row

    @staticmethod
    def _comments(session: Session, context: StaffReviewContext, round_id: str):
        return session.scalars(
            select(InternalReviewCommentRow)
            .where(*_scope(InternalReviewCommentRow, context), InternalReviewCommentRow.round_id == round_id)
            .order_by(
                InternalReviewCommentRow.timecode_ms, InternalReviewCommentRow.created_at, InternalReviewCommentRow.id
            )
        ).all()

    def _round(self, session: Session, context: StaffReviewContext, row: InternalReviewRoundRow) -> dict[str, object]:
        comments = self._comments(session, context, row.id)
        return {
            "id": row.id,
            "number": row.number,
            "title": row.title,
            "note": row.note,
            "projectId": row.project_id,
            "finalVideoVersionId": row.final_video_version_id,
            "state": row.state,
            "locked": row.locked,
            "version": row.version,
            "createdBy": row.created_by,
            "createdAt": _iso(row.created_at),
            "updatedAt": _iso(row.updated_at),
            "decisionNote": row.decision_note,
            "decidedBy": row.decided_by,
            "decidedAt": _iso(row.decided_at),
            "lockedAt": _iso(row.locked_at),
            "openComments": sum(item.parent_comment_id is None and item.status != "resolved" for item in comments),
            "totalComments": len(comments),
        }

    @staticmethod
    def _comment(row: InternalReviewCommentRow) -> dict[str, object]:
        return {
            "id": row.id,
            "roundId": row.round_id,
            "finalVideoVersionId": row.final_video_version_id,
            "authorId": row.author_id,
            "timecodeMs": row.timecode_ms,
            "shotId": row.shot_id,
            "body": row.body,
            "severity": row.severity,
            "parentCommentId": row.parent_comment_id,
            "status": row.status,
            "version": row.version,
            "createdAt": _iso(row.created_at),
            "updatedAt": _iso(row.updated_at),
        }

    @staticmethod
    def _final(session: Session, context: StaffReviewContext, row: FinalVideoVersionRow) -> dict[str, object]:
        timeline = session.scalar(
            select(TimelineVersionRow).where(
                *_scope(TimelineVersionRow, context),
                TimelineVersionRow.project_id == row.project_id,
                TimelineVersionRow.timeline_id == row.timeline_id,
                TimelineVersionRow.version_id == row.timeline_version_id,
            )
        )
        output = row.snapshot.get("output", {})
        preview = row.snapshot.get("preview", {})
        snapshot = {} if timeline is None else timeline.snapshot
        shots: list[dict[str, object]] = []
        for track in snapshot.get("tracks", []):
            if not isinstance(track, dict) or track.get("kind") != "video":
                continue
            for clip in track.get("clips", []):
                if (
                    isinstance(clip, dict)
                    and clip.get("shot_id")
                    and isinstance(clip.get("timeline_start_ms"), int)
                    and isinstance(clip.get("timeline_end_ms"), int)
                ):
                    shots.append(
                        {
                            "id": clip["shot_id"],
                            "clipId": clip.get("clip_id"),
                            "startMs": clip["timeline_start_ms"],
                            "endMs": clip["timeline_end_ms"],
                        }
                    )
        return {
            "id": row.version_id,
            "finalVideoId": row.final_video_id,
            "timelineId": row.timeline_id,
            "timelineVersionId": row.timeline_version_id,
            "episodeId": snapshot.get("episode_id", preview.get("episode_id")),
            "createdAt": _iso(row.created_at),
            "durationMs": output.get("duration_ms", preview.get("duration_ms")),
            "revision": None if timeline is None else timeline.revision,
            "shots": sorted(shots, key=lambda shot: (cast(int, shot["startMs"]), str(shot["id"]))),
        }

    @staticmethod
    def _audit(
        session: Session,
        context: StaffReviewContext,
        row: InternalReviewRoundRow,
        action: str,
        before: dict[str, object] | None,
        after: dict[str, object],
        result: dict[str, object],
        now: datetime,
    ) -> None:
        event_id = str(uuid4())
        event_type = f"review.internal.{action}"
        session.add(
            ReviewAuditRow(
                tenant_id=context.scope.tenant_id,
                workspace_id=context.scope.workspace_id,
                event_id=event_id,
                event_type=event_type,
                occurred_at=now,
                actor_id=context.trusted.actor_id,
                object_type="internal_review_round",
                object_id=row.id,
                request_id=context.trusted.request_id,
                before=before,
                after={**after, "result": result},
                result="succeeded",
            )
        )
        session.add(
            OutboxRow(
                id=event_id,
                tenant_id=context.scope.tenant_id,
                workspace_id=context.scope.workspace_id,
                event_type=event_type,
                aggregate_type="internal_review_round",
                aggregate_id=row.id,
                payload={
                    "projectId": row.project_id,
                    "roundId": row.id,
                    "finalVideoVersionId": row.final_video_version_id,
                    "actorId": context.trusted.actor_id,
                    "requestId": context.trusted.request_id,
                    "version": row.version,
                    "state": row.state,
                    "locked": row.locked,
                },
                schema_version=1,
                created_at=now,
            )
        )

    def _ticket(self, context: StaffReviewContext, row: InternalReviewRoundRow, expires: datetime) -> str:
        payload = {
            "tenant": context.scope.tenant_id,
            "workspace": context.scope.workspace_id,
            "project": row.project_id,
            "round": row.id,
            "final": row.final_video_version_id,
            "version": row.version,
            "actor": context.trusted.actor_id,
            "expires": int(expires.timestamp()),
        }
        raw = (
            base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(
            self.runtime._secret_key, f"internal-review-media:{raw}".encode(), hashlib.sha256
        ).hexdigest()
        return f"{raw}.{signature}"

    def media(self, round_id: str, ticket: str) -> ReviewMedia:
        from server.xingjing_review_persistence import ReviewPersistenceScope

        try:
            raw, signature = ticket.split(".", 1)
            expected = hmac.new(
                self.runtime._secret_key, f"internal-review-media:{raw}".encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            if (
                not isinstance(payload, dict)
                or payload.get("round") != round_id
                or type(payload.get("expires")) is not int
                or payload["expires"] <= int(datetime.now(UTC).timestamp())
                or not payload.get("actor")
            ):
                raise ValueError()
            scope = ReviewPersistenceScope(payload["tenant"], payload["workspace"])
        except (ValueError, TypeError, KeyError) as error:
            raise LinkUnavailable() from error
        with self.runtime._session_factory() as session:
            row = session.scalar(
                select(InternalReviewRoundRow).where(
                    InternalReviewRoundRow.tenant_id == scope.tenant_id,
                    InternalReviewRoundRow.workspace_id == scope.workspace_id,
                    InternalReviewRoundRow.id == round_id,
                    InternalReviewRoundRow.project_id == payload["project"],
                    InternalReviewRoundRow.final_video_version_id == payload["final"],
                    InternalReviewRoundRow.version == payload["version"],
                )
            )
            project = session.scalar(
                select(ProjectRow.id).where(
                    ProjectRow.tenant_id == scope.tenant_id,
                    ProjectRow.workspace_id == scope.workspace_id,
                    ProjectRow.id == payload["project"],
                    ProjectRow.deleted_at.is_(None),
                )
            )
            if row is None or project is None:
                raise LinkUnavailable()
        return self._verified_media(scope, payload["project"], payload["final"])

    def _verified_media(self, scope, project_id: str, version_id: str) -> ReviewMedia:
        from server.xingjing_review_runtime.runtime import ReviewRuntimeConfigurationError

        with self.runtime._session_factory() as session:
            final = session.scalar(
                select(FinalVideoVersionRow).where(
                    FinalVideoVersionRow.tenant_id == scope.tenant_id,
                    FinalVideoVersionRow.workspace_id == scope.workspace_id,
                    FinalVideoVersionRow.project_id == project_id,
                    FinalVideoVersionRow.version_id == version_id,
                )
            )
            if final is None:
                raise LinkUnavailable()
            output = final.snapshot.get("output")
            if (
                not isinstance(output, dict)
                or type(output.get("size_bytes")) is not int
                or output["size_bytes"] <= 0
                or type(output.get("duration_ms")) is not int
                or output["duration_ms"] <= 0
                or not isinstance(output.get("content_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", output["content_sha256"]) is None
            ):
                raise ReviewRuntimeConfigurationError("INTERNAL_REVIEW_MEDIA_METADATA_INVALID")
        return self.runtime._verified_media(scope, project_id, version_id, verify_digest=True)
