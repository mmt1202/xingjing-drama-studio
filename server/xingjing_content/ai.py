from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.xingjing_content_persistence import SqlAlchemyContentAiRequestRepository

from .models import (
    CharacterInsight,
    ContentAnalysis,
    DirectorProfile,
    RelationshipInsight,
    ScriptDocument,
    StoryBeat,
)
from .parser import parse_script

MAX_AI_INPUT_CHARS = 20_000
MAX_AI_INSTRUCTION_CHARS = 2_000
logger = logging.getLogger(__name__)


class ContentAiConfigurationUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("CONTENT_AI_CONFIGURATION_UNAVAILABLE")


class ContentAiInputTooLarge(ValueError):
    def __init__(
        self, actual: int, maximum: int = MAX_AI_INPUT_CHARS, *, scope: str = "current_script_version"
    ) -> None:
        super().__init__("CONTENT_AI_INPUT_TOO_LARGE")
        self.details = {"actualCharacters": actual, "maxCharacters": maximum, "inputScope": scope}


@dataclass(frozen=True, slots=True)
class AnalyzedScript:
    content: str
    analysis: ContentAnalysis


class _Character(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    appearances: int = Field(ge=0)
    description: str = Field(default="", max_length=2000)


class _Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    relation: str = Field(min_length=1, max_length=500)


class _StoryBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=3000)
    scene_ids: list[str] = Field(default_factory=list, max_length=200)


class _StructuredAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screenplay: str = Field(
        min_length=1,
        max_length=100_000,
        description="完整标准剧本文本。每个场景以 场景：地点｜时间｜内/外 开头，段落使用 动作：、旁白：或人物名：；不得省略正文。",
    )
    characters: list[_Character] = Field(default_factory=list, max_length=300)
    locations: list[str] = Field(default_factory=list, max_length=300)
    props: list[str] = Field(default_factory=list, max_length=300)
    relationships: list[_Relationship] = Field(default_factory=list, max_length=1000)
    story_beats: list[_StoryBeat] = Field(default_factory=list, max_length=300)
    episode_suggestions: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_screenplay(self):
        parsed = parse_script(self.screenplay, version_number=1, change_summary="validation")
        if parsed.validation_errors:
            raise ValueError("SCRIPT_AI_STRUCTURE_INVALID")
        scene_ids = {scene.scene_id for scene in parsed.scenes}
        if any(identity not in scene_ids for beat in self.story_beats for identity in beat.scene_ids):
            raise ValueError("SCRIPT_AI_SCENE_REFERENCE_INVALID")
        return self


class _DirectorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: str = Field(min_length=1, max_length=1000)
    pacing: str = Field(min_length=1, max_length=1000)
    visual_style: str = Field(min_length=1, max_length=1000)
    camera_language: str = Field(min_length=1, max_length=1000)
    production_constraints: list[str] = Field(default_factory=list, max_length=100)


class _RewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=2_000_000)
    change_summary: str = Field(min_length=1, max_length=2000)


class ContentAiService:
    def __init__(
        self,
        requests: SqlAlchemyContentAiRequestRepository,
        *,
        tenant_id: str,
        project_name: str,
    ) -> None:
        self._requests = requests
        self._tenant_id = tenant_id
        self._project_name = project_name

    async def readiness(self) -> dict[str, object]:
        """Resolve local model configuration without submitting text or recording usage."""
        try:
            await TextGenerator.create(TextTaskType.SCRIPT, self._project_name, read_only=True)
        except (ValueError, KeyError, FileNotFoundError):
            state = "configuration_unavailable"
        except Exception as error:
            logger.warning("Content AI readiness unavailable (%s)", type(error).__name__)
            state = "temporarily_unavailable"
        else:
            state = "ready"
        return {"ready": state == "ready", "status": state, "checksProviderConnection": False}

    async def analyze(
        self, document: ScriptDocument, *, expected_revision: int, idempotency_key: str
    ) -> AnalyzedScript | ContentAnalysis:
        payload = await self._generate(
            document,
            action="analyze",
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            schema=_StructuredAnalysis,
            system_prompt=(
                "你是影视编剧与结构分析师。把输入的小说或工作副本文本改编为完整、可执行的标准剧本，"
                "放入screenplay字段；每场以‘场景：地点｜时间｜内/外’开头，正文分为‘动作：’、‘旁白：’或‘人物名：’。"
                "章节标题可保留为Markdown标题；场景外不得遗留普通叙事。不得截断正文，不得虚构原文没有的情节或人物事实。"
                "同时提取人物、场景、道具、人物关系、情节点与分集建议；scene_ids必须使用输出场景顺序编号S001、S002等。"
                "新增或改写内容不得编造原小说坐标。输出必须严格符合 JSON Schema。"
            ),
            instruction="分析以下剧本并返回结构化内容：",
        )
        if "screenplay" not in payload:
            raise ValueError("CONTENT_AI_LEGACY_RESULT_REQUIRES_NEW_OPERATION")
        result = _StructuredAnalysis.model_validate(payload)
        base = parse_script(result.screenplay, version_number=1, change_summary="analysis").analysis
        analysis = ContentAnalysis(
            word_count=base.word_count,
            character_count=base.character_count,
            scene_count=base.scene_count,
            dialogue_count=base.dialogue_count,
            estimated_duration_ms=base.estimated_duration_ms,
            estimated_episode_count=max(base.estimated_episode_count, len(result.episode_suggestions)),
            sensitive_terms=base.sensitive_terms,
            characters=tuple(
                CharacterInsight(item.name, item.appearances, item.description) for item in result.characters
            )
            if result.characters
            else base.characters,
            locations=tuple(dict.fromkeys(item.strip() for item in result.locations if item.strip())) or base.locations,
            props=tuple(dict.fromkeys(item.strip() for item in result.props if item.strip())),
            relationships=tuple(
                RelationshipInsight(item.source, item.target, item.relation) for item in result.relationships
            ),
            story_beats=tuple(
                StoryBeat(item.label, item.summary, tuple(item.scene_ids)) for item in result.story_beats
            ),
            episode_suggestions=tuple(result.episode_suggestions),
        )
        return AnalyzedScript(result.screenplay, analysis)

    async def direct(
        self, document: ScriptDocument, *, expected_revision: int, idempotency_key: str
    ) -> DirectorProfile:
        if document.locked_version_number is None:
            raise ValueError("SCRIPT_NOT_FROZEN")
        payload = await self._generate(
            document,
            action="direct",
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            schema=_DirectorResult,
            system_prompt=(
                "你是影视项目总导演。根据输入剧本给出可执行的受众、节奏、视觉风格、"
                "镜头语言和制作约束。输出必须严格符合 JSON Schema。"
            ),
            instruction="为以下剧本生成导演档案：",
        )
        result = _DirectorResult.model_validate(payload)
        return DirectorProfile(
            audience=result.audience,
            pacing=result.pacing,
            visual_style=result.visual_style,
            camera_language=result.camera_language,
            production_constraints=tuple(result.production_constraints),
        )

    async def rewrite(
        self,
        document: ScriptDocument,
        *,
        expected_revision: int,
        instruction: str,
        idempotency_key: str,
    ) -> tuple[str, str]:
        if not instruction.strip():
            raise ValueError("REWRITE_INSTRUCTION_REQUIRED")
        payload = await self._generate(
            document,
            action="rewrite",
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            schema=_RewriteResult,
            system_prompt=(
                "你是专业影视编剧。必须保留原文可追踪性，不改变未要求修改的事实，"
                "输出完整改写后剧本和简明变更摘要，严格符合 JSON Schema。"
            ),
            instruction=f"改写要求：{instruction.strip()}\n完整剧本：",
            instruction_characters=len(instruction),
        )
        result = _RewriteResult.model_validate(payload)
        return result.content, result.change_summary

    async def _generate(
        self,
        document: ScriptDocument,
        *,
        action: str,
        expected_revision: int,
        idempotency_key: str,
        schema: type[BaseModel],
        system_prompt: str,
        instruction: str,
        instruction_characters: int | None = None,
    ) -> dict[str, object]:
        source = document.current_version.source_content
        fingerprint = sha256(
            json.dumps(
                {
                    "action": action,
                    "scriptId": document.script_id,
                    "expectedRevision": expected_revision,
                    "instruction": instruction,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        cached = self._requests.completed_result(
            tenant_id=self._tenant_id,
            workspace_id=document.workspace_id,
            project_id=document.project_id,
            action=action,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        if cached is not None:
            return {key: value for key, value in cached.items() if not key.startswith("_")}
        if action in {"analyze", "rewrite"} and document.locked_version_number is not None:
            raise ValueError("SCRIPT_FROZEN")
        if document.revision != expected_revision:
            raise ValueError("VERSION_CONFLICT")
        if len(source) > MAX_AI_INPUT_CHARS:
            raise ContentAiInputTooLarge(len(source))
        if instruction_characters is not None and instruction_characters > MAX_AI_INSTRUCTION_CHARS:
            raise ContentAiInputTooLarge(instruction_characters, MAX_AI_INSTRUCTION_CHARS, scope="rewrite_instruction")
        now = datetime.now(UTC)
        claim = self._requests.claim(
            tenant_id=self._tenant_id,
            workspace_id=document.workspace_id,
            project_id=document.project_id,
            action=action,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            now=now,
        )
        if claim.status == "succeeded":
            if claim.result_payload is None:
                raise ValueError("CONTENT_AI_RESULT_MISSING")
            return {key: value for key, value in claim.result_payload.items() if not key.startswith("_")}
        if not claim.claimed:
            raise ValueError("CONTENT_AI_REQUEST_IN_PROGRESS")
        try:
            try:
                generator = await TextGenerator.create(TextTaskType.SCRIPT, self._project_name)
            except (ValueError, KeyError, FileNotFoundError) as error:
                raise ContentAiConfigurationUnavailable() from error
            generated = await generator.generate(
                TextGenerationRequest(
                    system_prompt=system_prompt,
                    prompt=f"{instruction}\n{source}",
                    response_schema=schema,
                    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                ),
                project_name=self._project_name,
            )
            raw_responses = [generated.text]
            repaired = False
            try:
                result = schema.model_validate_json(strip_json_code_fences(generated.text))
            except ValidationError:
                if len(generated.text) > 50_000:
                    raise ValueError("CONTENT_AI_RESPONSE_TOO_LARGE_TO_REPAIR")
                repaired = True
                repair_instruction = json.dumps(
                    {
                        "instruction": "只修复下面响应的 JSON 结构，使其严格符合 schema；不得扩写业务内容。",
                        "schema": schema.model_json_schema(),
                        "invalidResponse": generated.text,
                    },
                    ensure_ascii=False,
                )
                repaired_generation = await generator.generate(
                    TextGenerationRequest(
                        system_prompt="你是 JSON 结构修复器。只返回合法 JSON，不要添加 Markdown。",
                        prompt=repair_instruction,
                        response_schema=schema,
                        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                    ),
                    project_name=self._project_name,
                )
                raw_responses.append(repaired_generation.text)
                result = schema.model_validate_json(strip_json_code_fences(repaired_generation.text))
            payload = result.model_dump(mode="json")
            payload["_provider"] = generated.provider
            payload["_model"] = generated.model
            prompt_snapshot = {
                "action": action,
                "templateKey": f"content.{action}",
                "templateVersion": 1,
                "systemPromptSha256": sha256(system_prompt.encode()).hexdigest(),
                "instructionSha256": sha256(instruction.encode()).hexdigest(),
                "schemaSha256": sha256(
                    json.dumps(schema.model_json_schema(), sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
                "source": "xingjing_content.ai",
                "repairAttempted": repaired,
                "repairAttempts": 1 if repaired else 0,
                "rawResponseSha256": [sha256(value.encode()).hexdigest() for value in raw_responses],
            }
            self._requests.succeed(
                tenant_id=self._tenant_id,
                workspace_id=document.workspace_id,
                project_id=document.project_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                payload=payload,
                now=datetime.now(UTC),
                raw_response=json.dumps(raw_responses, ensure_ascii=False),
                evidence=prompt_snapshot,
            )
            return {key: value for key, value in payload.items() if not key.startswith("_")}
        except Exception as error:
            self._requests.fail(
                tenant_id=self._tenant_id,
                workspace_id=document.workspace_id,
                project_id=document.project_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                reason=str(error),
                now=datetime.now(UTC),
            )
            if isinstance(error, ContentAiConfigurationUnavailable):
                raise
            raise ValueError("CONTENT_AI_MODEL_FAILED") from error
