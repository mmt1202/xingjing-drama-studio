from __future__ import annotations

import re
from io import BytesIO
from pathlib import PurePath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_SCRIPT_BYTES = 20 * 1024 * 1024
_SUPPORTED = {".txt", ".md", ".markdown", ".docx"}
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_script_text(filename: str, content: bytes) -> tuple[str, str]:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in _SUPPORTED:
        raise ValueError("SCRIPT_FILE_TYPE_UNSUPPORTED")
    if not content:
        raise ValueError("SCRIPT_FILE_EMPTY")
    if len(content) > MAX_SCRIPT_BYTES:
        raise ValueError("SCRIPT_FILE_TOO_LARGE")
    if suffix == ".docx":
        text = _docx_text(content)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        text = _decode_text(content)
        media_type = "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
    normalized = _normalize(text)
    if not normalized:
        raise ValueError("SCRIPT_CONTENT_EMPTY")
    return normalized, media_type


def _decode_text(content: bytes) -> str:
    if b"\x00" in content:
        raise ValueError("SCRIPT_FILE_BINARY")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("SCRIPT_FILE_ENCODING_UNSUPPORTED")


def _docx_text(content: bytes) -> str:
    try:
        with ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError, OSError) as error:
        raise ValueError("DOCX_INVALID") from error
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise ValueError("DOCX_INVALID") from error
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        value = "".join(node.text or "" for node in paragraph.iter(f"{_WORD_NS}t")).strip()
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _normalize(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()
