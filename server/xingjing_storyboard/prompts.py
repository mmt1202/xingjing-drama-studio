from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .errors import ContractViolation

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    prompt_id: str
    tenant_id: str
    workspace_id: str
    project_id: str
    name: str
    media_type: str
    template: str
    negative_prompt: str
    variables: tuple[str, ...]
    model_adapter_versions: tuple[str, ...]
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.prompt_id, self.tenant_id, self.workspace_id, self.project_id, self.name, self.template)):
            raise ContractViolation("PROMPT_TEMPLATE_REQUIRED")
        if self.media_type not in {"image", "video", "both"}:
            raise ContractViolation("PROMPT_MEDIA_TYPE_INVALID")
        declared = set(self.variables)
        used = set(_VARIABLE.findall(self.template)) | set(_VARIABLE.findall(self.negative_prompt))
        if len(declared) != len(self.variables) or declared != used:
            raise ContractViolation("PROMPT_VARIABLES_MISMATCH", details={"declared": sorted(declared), "used": sorted(used)})
        if self.version < 1:
            raise ContractViolation("INVALID_PROMPT_VERSION")

    def render(self, values: dict[str, str], *, adapter_version: str | None) -> tuple[str, str]:
        if set(values) != set(self.variables) or any(not value.strip() for value in values.values()):
            raise ContractViolation("PROMPT_VARIABLE_VALUES_INVALID")
        if adapter_version and adapter_version not in self.model_adapter_versions:
            raise ContractViolation("PROMPT_ADAPTER_VERSION_UNSUPPORTED")

        def substitute(source: str) -> str:
            return _VARIABLE.sub(lambda match: values[match.group(1)], source)

        return substitute(self.template), substitute(self.negative_prompt)

    def to_dict(self, *, usage_count: int = 0) -> dict[str, object]:
        return {
            "id": self.prompt_id,
            "name": self.name,
            "mediaType": self.media_type,
            "template": self.template,
            "negativePrompt": self.negative_prompt,
            "variables": list(self.variables),
            "modelAdapterVersions": list(self.model_adapter_versions),
            "version": self.version,
            "usageCount": usage_count,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "archivedAt": self.archived_at.isoformat() if self.archived_at else None,
        }
