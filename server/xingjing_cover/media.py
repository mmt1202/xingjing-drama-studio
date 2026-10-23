"""Verify managed cover bytes before preview, selection and formal packaging."""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Mapping
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .contracts import CoverFailure


def verify_cover_file(path: Path | str, frozen_cover: Mapping[str, object]) -> dict[str, object]:
    source = Path(path)
    expected_size = frozen_cover.get("sizeBytes")
    expected_hash = frozen_cover.get("contentSha256")
    if type(expected_size) is not int or expected_size <= 0 or expected_size > 32 * 1024 * 1024:
        raise CoverFailure("COVER_MEDIA_METADATA_INVALID", 503)
    try:
        if not source.is_file() or source.stat().st_size != expected_size:
            raise CoverFailure("COVER_MEDIA_SIZE_MISMATCH", 503)
        digest = hashlib.sha256()
        with source.open("rb") as content:
            while chunk := content.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise CoverFailure("COVER_MEDIA_HASH_MISMATCH", 503)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                width, height = image.size
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format or "")
                if mime is None or width < 1 or height < 1 or width * height > 40_000_000:
                    raise CoverFailure("COVER_MEDIA_FORMAT_INVALID", 422)
                image.verify()
        for key, actual in (("width", width), ("height", height), ("mimeType", mime)):
            if key in frozen_cover and frozen_cover[key] != actual:
                raise CoverFailure("COVER_MEDIA_METADATA_MISMATCH", 503)
        return {**frozen_cover, "width": width, "height": height, "mimeType": mime}
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise CoverFailure("COVER_MEDIA_UNAVAILABLE", 503) from error
