"""Resolve one explicitly granted deployment credential, never the active default."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC

from cryptography.exceptions import InvalidTag
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.credential import ProviderCredential
from lib.image_backends.openai import OpenAIImageBackend

_REFERENCE = re.compile(r"^secret://provider-credential/([1-9][0-9]*)$")


class NativeImageConfigurationError(ValueError):
    pass


class ExactImageCredentialResolver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], grants: Mapping[str, tuple[str, ...]]) -> None:
        self.sessions = sessions
        self.grants = dict(grants)

    @staticmethod
    def parse_grants(raw: str) -> dict[str, tuple[str, ...]]:
        try:
            value = json.loads(raw or "{}")
            if not isinstance(value, dict) or any(
                not isinstance(k, str)
                or not isinstance(v, list)
                or any(not isinstance(ref, str) or not _REFERENCE.fullmatch(ref) for ref in v)
                for k, v in value.items()
            ):
                raise ValueError()
            return {key: tuple(refs) for key, refs in value.items()}
        except (ValueError, TypeError) as error:
            raise NativeImageConfigurationError("GENERATION_NATIVE_CREDENTIAL_GRANTS_INVALID") from error

    async def backend(
        self, workspace_id: str, provider_id: str, model_id: str, reference: str, *, revision: str | None = None
    ) -> tuple[OpenAIImageBackend, str]:
        match = _REFERENCE.fullmatch(reference)
        if provider_id != "openai" or not match:
            raise NativeImageConfigurationError("GENERATION_NATIVE_MODEL_UNSUPPORTED")
        if reference not in self.grants.get(workspace_id, ()):
            raise NativeImageConfigurationError("GENERATION_NATIVE_CREDENTIAL_NOT_GRANTED")
        try:
            async with self.sessions() as session:
                credential = await session.get(ProviderCredential, int(match.group(1)))
                if (
                    credential is None
                    or credential.provider != provider_id
                    or not credential.is_active
                    or not credential.api_key
                ):
                    raise NativeImageConfigurationError("GENERATION_NATIVE_CREDENTIAL_UNAVAILABLE")
                updated = credential.updated_at
                current_revision = (
                    updated.replace(tzinfo=UTC).isoformat()
                    if updated.tzinfo is None
                    else updated.astimezone(UTC).isoformat()
                )
                if revision is not None and revision != current_revision:
                    raise NativeImageConfigurationError("GENERATION_NATIVE_CREDENTIAL_CHANGED")
                # This backend receives only the exact selected row. No ConfigResolver active-key overlay.
                backend = OpenAIImageBackend(
                    api_key=credential.api_key, model=model_id, base_url=credential.base_url, automatic_retries=False
                )
            return backend, current_revision
        except NativeImageConfigurationError:
            raise
        except (RuntimeError, ValueError, TypeError, SQLAlchemyError, InvalidTag) as error:
            raise NativeImageConfigurationError("GENERATION_NATIVE_CREDENTIAL_UNAVAILABLE") from error
