"""Seed imported source in the same transaction as project activation."""

import asyncio
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from server.xingjing_content.models import DirectorProfile, ScriptDocument, SourceDocument
from server.xingjing_content.parser import parse_script
from server.xingjing_content_persistence.repository import AuditContext, SqlAlchemyContentRepository
from server.xingjing_identity_context import TrustedWorkspaceContext

from .setup import ProjectSetup


async def initialize_source(
    session: AsyncSession,
    context: TrustedWorkspaceContext,
    project_id: str,
    name: str,
    setup: ProjectSetup | None,
) -> None:
    if setup is None or not setup.source_text.strip():
        return
    version = await asyncio.to_thread(
        parse_script, setup.source_text, version_number=1, change_summary="Project source"
    )
    document = ScriptDocument(
        script_id=str(uuid5(NAMESPACE_URL, f"project-source:{context.workspace_id}:{project_id}")),
        workspace_id=context.workspace_id,
        project_id=project_id,
        title=name,
        revision=1,
        source_document=SourceDocument("source.txt", "text/plain", setup.source_text),
        versions=(version,),
        director_profile=DirectorProfile(
            visual_style=setup.visual_style or None, camera_language=setup.director_style or None
        ),
    )

    def persist(sync_session: Session) -> None:
        repository = SqlAlchemyContentRepository(
            lambda: sync_session,
            audit_context=AuditContext(context.request_id, context.actor_id, datetime.now(UTC)),
        )
        repository.add_initial_document(sync_session, document, idempotency_key=f"project-source:{project_id}")

    await session.run_sync(persist)
