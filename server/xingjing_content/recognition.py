"""Recognize source structure without sending an author's text to a model."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lib.source_structure import heading_title

if TYPE_CHECKING:
    from .models import ScriptDocument


_LABEL = re.compile(r"^(?:场景|場景|scene|cảnh)\s*[:：]\s*(\S.*)$", re.IGNORECASE)
_NUMBERED = re.compile(
    r"^(?:(?:第[零〇一二三四五六七八九十百千万两\d]+[场場]|(?:场次|場次|场景|scene|cảnh)\s*[\d一二三四五六七八九十]+)"
    r"|(?P<number>\d{1,3}[-－.]\d{1,3}))(?=[\s:：.、|｜(（])[\s:：.、|｜]*(.+)$",
    re.I,
)
_SLUG = re.compile(r"^(INT\./EXT\.|EXT\./INT\.|INT\.|EXT\.)\s*(\S.*)$", re.I)
_CN_SLUG = re.compile(r"^(内景|外景|內景)[\s·.：:、｜|]+(\S.*)$")
_TIME = {"日", "夜", "晨", "昏", "白天", "夜晚", "清晨", "黄昏", "傍晚", "上午", "下午", "午夜", "黎明"}
_SETTING = {"内", "外", "內", "内景", "外景", "內景", "内外"}
_PATH = re.compile(
    r"""^(?:[a-z]:[\\/]|\\\\[^\\/]+[\\/]|file:/+|(?:/|~/|\.\.?[\\/]))[^\r\n]+\.(?:txt|md|markdown|docx?|pdf|epub|rtf)$""",
    re.I,
)
_EPISODE = re.compile(r"^第[零〇一二三四五六七八九十百千万两\d]+[集幕](?:\s+[^。！？!?]{1,60})?$")


def path_only(text: str) -> bool:
    value = text.strip()
    return len(value) <= 4096 and bool(_PATH.fullmatch(value.strip("\"'“”")))


def _parts(
    body: str, *, setting: str | None = None, numbered: bool = False
) -> tuple[str, str | None, str | None] | None:
    if len(body) > 180 or re.search(r"[。！？!?]", body):
        return None
    words = re.split(r"[\s｜|·/、,，()（）]+", body.strip())
    location = []
    time = None
    for word in filter(None, words):
        if word in _TIME:
            time = word
        elif word in _SETTING:
            setting = word.replace("內", "内").removesuffix("景")
        elif len(word) == 2 and word[0] in _TIME and word[1] in _SETTING:
            time, setting = word[0], word[1].replace("內", "内")
        else:
            location.append(word)
    if not location or numbered and not (time or setting):
        return None
    return " ".join(location), time, setting


def scene_heading(line: str) -> tuple[str, str | None, str | None] | None:
    line = line.strip()
    if line.startswith("#"):
        line = re.sub(r"^#{1,6}\s*", "", line)
    explicit = _LABEL.match(line)
    if explicit:
        body = explicit[1].strip()
        if "|" in body or "｜" in body:
            parts = [part.strip() for part in re.split(r"[|｜]", body)]
            return parts[0], parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None
        return _parts(body) or (body, None, None)
    if len(line) > 200:
        return None
    if match := _SLUG.match(line):
        location, *time = re.split(r"\s+[-–—]\s+", match[2], maxsplit=1)
        return location.strip(), time[0].strip() if time else None, match[1].upper().strip(".")
    if match := _CN_SLUG.match(line):
        return _parts(match[2], setting=match[1][0].replace("內", "内"))
    if match := _NUMBERED.match(line):
        return _parts(match[2], numbered=bool(match["number"]))
    return None


def cover_line(line: str) -> bool:
    return bool(
        re.match(r"^#{1,6}\s+\S", line)
        or _EPISODE.fullmatch(line)
        or re.fullmatch(r"(?:剧名|片名|剧本名)[：:]\s*\S.{0,99}", line)
    )


def source_recognition(document: ScriptDocument) -> dict[str, object]:
    version = document.current_version
    text = version.source_content
    chapter_count = scene_count = paragraphs = 0
    headings_changed = prefix_content = False
    titles: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if heading := scene_heading(line):
            if scene_count < len(version.scenes):
                stored = version.scenes[scene_count].heading
                headings_changed |= heading != (stored.location, stored.time_of_day, stored.setting)
            scene_count += 1
            continue
        if not scene_count and not prefix_content and not cover_line(line):
            prefix_content = True
        if title := heading_title(line):
            chapter_count += 1
            if len(titles) < 5:
                titles.append(title)
        else:
            paragraphs += 1
    kind = "prose"
    if path_only(text):
        kind = "path"
    elif scene_count:
        kind = "screenplay"
    elif chapter_count:
        kind = "chapters"
    stored_prefix_issue = any(issue.code == "SCRIPT_PREFIX_CONTENT_UNPARSED" for issue in version.validation_errors)
    return {
        "scriptId": document.script_id,
        "revision": document.revision,
        "version": version.number,
        "kind": kind,
        "requiresReparse": bool(scene_count)
        and (headings_changed or scene_count != len(version.scenes) or prefix_content != stored_prefix_issue),
        "characterCount": len(text),
        "chapterCount": chapter_count,
        "chapterTitles": titles,
        "sceneCount": scene_count,
        "paragraphCount": paragraphs,
    }
