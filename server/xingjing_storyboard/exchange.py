from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self, TypedDict, cast
from xml.etree import ElementTree as ET

from .errors import ImportValidationError
from .models import AssetReference, GenerationStatus, Shot, Storyboard


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


_HEADERS = (
    "shot_id",
    "position",
    "shot_number",
    "shot_size",
    "camera_movement",
    "dialogue",
    "duration_ms",
    "prompt",
    "model_strategy",
    "cost_tier",
    "asset_references",
    "selected_media_id",
    "generation_status",
)
_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


class ShotRow(TypedDict):
    shot_id: str
    position: str
    shot_number: str
    shot_size: str
    camera_movement: str
    dialogue: str
    duration_ms: str
    prompt: str
    model_strategy: str
    cost_tier: str
    asset_references: str
    selected_media_id: str
    generation_status: str


@dataclass(frozen=True, slots=True)
class ExportResult:
    format: ExportFormat
    filename: str
    media_type: str
    content: bytes
    sha256: str
    replayed: bool = False

    @classmethod
    def create(cls, *, format: ExportFormat, filename: str, media_type: str, content: bytes) -> Self:
        return cls(format, filename, media_type, content, hashlib.sha256(content).hexdigest())

    def to_storage(self) -> dict[str, object]:
        return {
            "format": self.format.value,
            "filename": self.filename,
            "media_type": self.media_type,
            "content_base64": base64.b64encode(self.content).decode("ascii"),
            "sha256": self.sha256,
        }

    @classmethod
    def from_storage(cls, value: Mapping[str, object], *, replayed: bool) -> Self:
        try:
            content = base64.b64decode(_required_text(value, "content_base64"), validate=True)
            result = cls(
                format=ExportFormat(_required_text(value, "format")),
                filename=_required_text(value, "filename"),
                media_type=_required_text(value, "media_type"),
                content=content,
                sha256=_required_text(value, "sha256"),
                replayed=replayed,
            )
        except (ValueError, UnicodeError) as error:
            raise ImportValidationError("INVALID_EXPORT_RECEIPT") from error
        if result.sha256 != hashlib.sha256(content).hexdigest():
            raise ImportValidationError("INVALID_EXPORT_RECEIPT")
        return result


@dataclass(frozen=True, slots=True)
class ImportedShot:
    shot_id: str | None
    position: int
    shot_number: str
    shot_size: str | None
    camera_movement: str | None
    dialogue: str
    duration_ms: int
    prompt: str
    model_strategy: str | None
    cost_tier: str | None
    asset_references: tuple[AssetReference, ...]
    selected_media_id: str | None
    generation_status: GenerationStatus


def export_storyboard(storyboard: Storyboard, format: ExportFormat) -> ExportResult:
    stem = f"storyboard-{storyboard.storyboard_id}-v{storyboard.version}"
    if format is ExportFormat.JSON:
        content = json.dumps(
            {
                "schema_version": 1,
                "storyboard_id": storyboard.storyboard_id,
                "version": storyboard.version,
                "shots": [shot.to_dict() for shot in storyboard.shots],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return ExportResult.create(
            format=format, filename=f"{stem}.json", media_type="application/json", content=content
        )
    rows = [_shot_row(shot) for shot in storyboard.shots]
    if format is ExportFormat.CSV:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_HEADERS)
        writer.writerows(tuple(row[header] for header in _HEADERS) for row in rows)
        return ExportResult.create(
            format=format,
            filename=f"{stem}.csv",
            media_type="text/csv; charset=utf-8",
            content=stream.getvalue().encode("utf-8-sig"),
        )
    if format is ExportFormat.XLSX:
        return ExportResult.create(
            format=format,
            filename=f"{stem}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=_xlsx_bytes(rows),
        )
    raise AssertionError(f"unhandled export format: {format}")


def import_shots(filename: str, content: bytes) -> tuple[ImportedShot, ...]:
    suffix = Path(filename).suffix.casefold()
    try:
        if suffix == ".json":
            return _parse_json(content)
        if suffix == ".csv":
            return _parse_rows(_csv_rows(content))
        if suffix == ".xlsx":
            return _parse_rows(_xlsx_rows(content))
    except ImportValidationError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, zipfile.BadZipFile, ET.ParseError) as error:
        raise ImportValidationError("INVALID_IMPORT") from error
    raise ImportValidationError("UNSUPPORTED_IMPORT_FORMAT")


def _parse_json(content: bytes) -> tuple[ImportedShot, ...]:
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ImportValidationError("INVALID_IMPORT") from error
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise ImportValidationError("INVALID_IMPORT")
    shots = value.get("shots")
    if not isinstance(shots, list):
        raise ImportValidationError("INVALID_IMPORT")
    return _parse_rows([_mapping(item) for item in shots])


def _csv_rows(content: bytes) -> list[dict[str, object]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != _HEADERS:
        raise ImportValidationError("INVALID_IMPORT")
    return [{key: value for key, value in row.items()} for row in reader]


def _parse_rows(rows: Sequence[Mapping[str, object]]) -> tuple[ImportedShot, ...]:
    imported = tuple(_parse_row(row) for row in rows)
    populated_ids = [shot.shot_id for shot in imported if shot.shot_id]
    if len(set(populated_ids)) != len(populated_ids):
        raise ImportValidationError("DUPLICATE_SHOT_ID")
    positions = [shot.position for shot in imported]
    if set(positions) != set(range(1, len(imported) + 1)):
        raise ImportValidationError("INVALID_IMPORT")
    return tuple(sorted(imported, key=lambda shot: shot.position))


def _parse_row(row: Mapping[str, object]) -> ImportedShot:
    if not set(_HEADERS) <= set(row):
        raise ImportValidationError("INVALID_IMPORT")
    raw_refs = row["asset_references"]
    refs_value = json.loads(raw_refs) if isinstance(raw_refs, str) else raw_refs
    if not isinstance(refs_value, list):
        raise ImportValidationError("INVALID_IMPORT")
    try:
        return ImportedShot(
            shot_id=_nullable_text(row["shot_id"]),
            position=_integer(row["position"]),
            shot_number=_text(row["shot_number"]),
            shot_size=_nullable_text(row["shot_size"]),
            camera_movement=_nullable_text(row["camera_movement"]),
            dialogue=_text(row["dialogue"]),
            duration_ms=_integer(row["duration_ms"]),
            prompt=_text(row["prompt"]),
            model_strategy=_nullable_text(row["model_strategy"]),
            cost_tier=_nullable_text(row["cost_tier"]),
            asset_references=tuple(AssetReference.from_dict(_mapping(item)) for item in refs_value),
            selected_media_id=_nullable_text(row["selected_media_id"]),
            generation_status=GenerationStatus(_text(row["generation_status"])),
        )
    except (TypeError, ValueError) as error:
        raise ImportValidationError("INVALID_IMPORT") from error


def _shot_row(shot: Shot) -> ShotRow:
    return {
        "shot_id": shot.shot_id,
        "position": str(shot.position),
        "shot_number": shot.shot_number,
        "shot_size": shot.shot_size or "",
        "camera_movement": shot.camera_movement or "",
        "dialogue": shot.dialogue,
        "duration_ms": str(shot.duration_ms),
        "prompt": shot.prompt,
        "model_strategy": shot.model_strategy or "",
        "cost_tier": shot.cost_tier or "",
        "asset_references": json.dumps([item.to_dict() for item in shot.asset_references], ensure_ascii=False),
        "selected_media_id": shot.selected_media_id or "",
        "generation_status": shot.generation_status.value,
    }


def _xlsx_bytes(rows: Sequence[ShotRow]) -> bytes:
    ET.register_namespace("", _SPREADSHEET_NS)
    worksheet = ET.Element(f"{{{_SPREADSHEET_NS}}}worksheet")
    sheet_data = ET.SubElement(worksheet, f"{{{_SPREADSHEET_NS}}}sheetData")
    for row_index in range(1, len(rows) + 2):
        row = ET.SubElement(sheet_data, f"{{{_SPREADSHEET_NS}}}row", {"r": str(row_index)})
        values = _HEADERS if row_index == 1 else tuple(rows[row_index - 2][header] for header in _HEADERS)
        for column_index, value in enumerate(values, 1):
            cell = ET.SubElement(
                row,
                f"{{{_SPREADSHEET_NS}}}c",
                {"r": f"{_column_name(column_index)}{row_index}", "t": "inlineStr"},
            )
            inline = ET.SubElement(cell, f"{{{_SPREADSHEET_NS}}}is")
            ET.SubElement(inline, f"{{{_SPREADSHEET_NS}}}t").text = value
    parts = {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": b'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Storyboard" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return stream.getvalue()


def _xlsx_rows(content: bytes) -> list[dict[str, object]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = _shared_strings(archive)
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in root.findall(f".//{{{_SPREADSHEET_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{_SPREADSHEET_NS}}}c"):
            column = _column_index(cell.get("r", ""))
            cell_type = cell.get("t")
            if cell_type == "inlineStr":
                value = "".join(cell.itertext())
            else:
                raw = cell.findtext(f"{{{_SPREADSHEET_NS}}}v") or ""
                value = shared[int(raw)] if cell_type == "s" and raw else raw
            values[column] = value
        rows.append([values.get(index, "") for index in range(1, len(_HEADERS) + 1)])
    if not rows or tuple(rows[0]) != _HEADERS:
        raise ImportValidationError("INVALID_IMPORT")
    return [dict(zip(_HEADERS, row, strict=True)) for row in rows[1:]]


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(item.itertext()) for item in root.findall(f"{{{_SPREADSHEET_NS}}}si")]


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _column_index(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    if not letters:
        raise ImportValidationError("INVALID_IMPORT")
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter.upper()) - 64
    return result


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ImportValidationError("INVALID_IMPORT")
    return cast(Mapping[str, object], value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    return _text(value.get(key))


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ImportValidationError("INVALID_IMPORT")
    return value


def _nullable_text(value: object) -> str | None:
    if value is None:
        return None
    text = _text(value)
    return text or None


def _integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ImportValidationError("INVALID_IMPORT")
