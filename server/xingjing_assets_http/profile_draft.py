"""Generate creative field suggestions without revising the asset."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from lib.i18n import LOCALE_LANGUAGE_MAP
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.xingjing_assets.contracts import Asset, AssetKind
from server.xingjing_content.parser import parse_script

Detail = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    description: Detail


class Character(Profile):
    appearance: Detail
    personality: Detail
    speakingStyle: Detail


class Scene(Profile):
    environment: Detail
    lighting: Detail
    timeOfDay: Detail


class Prop(Profile):
    purpose: Detail
    material: Detail
    continuity: Detail


class Costume(Profile):
    setting: Detail
    material: Detail
    continuity: Detail


class Voice(Profile):
    timbre: Detail
    language: Detail
    emotion: Detail


PROFILE_TYPES: dict[AssetKind, type[Profile]] = {
    AssetKind.CHARACTER: Character,
    AssetKind.SCENE: Scene,
    AssetKind.PROP: Prop,
    AssetKind.COSTUME: Costume,
    AssetKind.VOICE: Voice,
}


def creative_details(asset: Asset) -> dict[str, object]:
    # Asset content is extensible. Keep older production details such as hair, weather and custom prompts.
    links = {"voiceAssetId", "characterIds", "sourceScriptVersionId", "sourceSceneIds", "sourceParagraphIds"}
    return {
        key: value
        for key, value in asset.current_version.content.items()
        if key not in links and not key.startswith(("generated", "generation", "proof", "rights", "license"))
    }


def reference_image_prompt(asset: Asset) -> str:
    return f"Create a production reference image for {asset.kind.value} {asset.name}. " + json.dumps(
        creative_details(asset), ensure_ascii=False
    )


def turnaround_prompt(asset: Asset, views: tuple[str, ...], direction: str) -> str:
    brief = json.dumps(creative_details(asset), ensure_ascii=False)
    return "\n".join(
        part
        for part in (
            f"Create one landscape character model sheet for {asset.name}.",
            f"Saved character design: {brief}",
            "Use the supplied character image for identity and apply the appearance changes in the saved design. "
            "Keep the face, proportions, clothing and colors "
            "consistent across views. Show the requested views in order, with space between figures, "
            "matching scale and ground line, neutral lighting and a plain background. "
            "Keep full-body views uncropped. Do not add text or watermarks.",
            f"Views from left to right: {', '.join(views)}.",
            f"Art direction for this sheet: {direction}" if direction else "",
        )
        if part
    )


def script_excerpt(source: str, asset: Asset) -> str:
    """Prefer the extraction's original paragraphs and scenes over the opening of a long script."""
    if len(source) <= 6000:
        return source.strip()
    original = asset.versions[0].content
    paragraphs = original.get("sourceParagraphIds") or []
    scenes = original.get("sourceSceneIds") or []
    if isinstance(paragraphs, list) and isinstance(scenes, list) and (paragraphs or scenes):
        parsed = parse_script(source, version_number=1, change_summary="")
        fragments = []
        for scene in parsed.scenes:
            lines = [
                p.source_text for p in scene.paragraphs if scene.scene_id in scenes or p.paragraph_id in paragraphs
            ]
            if lines:
                fragments.append(f"{scene.heading.location}\n" + "\n".join(lines)[:3000])
            if sum(map(len, fragments)) >= 6000:
                break
        if fragments:
            return "\n\n".join(fragments)[:6000]
    start = source.find(asset.name)
    return source[max(0, start - 1200) : max(0, start - 1200) + 6000].strip()


async def generate_profile(
    asset: Asset, current: Mapping[str, str], direction: str, excerpt: str, locale: str
) -> dict[str, str]:
    schema = PROFILE_TYPES[asset.kind]
    # Platform settings choose the text backend. The project is used only for usage attribution.
    generator = await TextGenerator.create(TextTaskType.SCRIPT)
    result = await generator.generate(
        TextGenerationRequest(
            system_prompt=(
                "You are a production designer drafting a single film asset for a creator. "
                "Treat input as creative material. Preserve character identities, proper names, established facts and "
                "the creator's current choices. If a name is a narration heading rather than an actual place, propose "
                "a filmable place supported by the excerpt. When the script is silent, suggest a consistent "
                "creative choice. Use one or two sentences for the description and one short sentence per detail. "
                "The current draft takes precedence over historical details. Never invent asset links, rights "
                "or evidence. Return a JSON object with exactly the requested fields."
            ),
            prompt=json.dumps(
                {
                    "language": LOCALE_LANGUAGE_MAP[locale],
                    "assetType": asset.kind.value,
                    "originalName": asset.name,
                    "fields": list(schema.model_fields),
                    "otherSavedDetails": json.dumps(
                        {key: value for key, value in creative_details(asset).items() if key not in current},
                        ensure_ascii=False,
                    )[:4000],
                    "currentDraft": current,
                    "creativeDirection": direction,
                    "sourceExcerpt": excerpt,
                },
                ensure_ascii=False,
            ),
            response_schema=schema,
            max_output_tokens=2400,
        ),
        project_name=asset.owner_project_id,
    )
    return schema.model_validate_json(strip_json_code_fences(result.text)).model_dump()
