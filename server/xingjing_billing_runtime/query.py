from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class BillingPageQuery:
    page_size: int = 25
    page_token: str | None = None
    query: str | None = None
    project_id: str | None = None
    source: str | None = None
    model_id: str | None = None
    actor_id: str | None = None
    episode_id: str | None = None
    shot_id: str | None = None
    status: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.page_size < 1 or self.page_size > 100:
            raise ValueError("PAGE_SIZE_INVALID")
        if self.source not in (None, "generation", "audio", "editing"):
            raise ValueError("BILLING_SOURCE_INVALID")
        for instant in (self.date_from, self.date_to):
            if instant is not None and (instant.tzinfo is None or instant.utcoffset() is None):
                raise ValueError("BILLING_DATE_TIMEZONE_REQUIRED")
        if self.date_from is not None and self.date_to is not None and self.date_from >= self.date_to:
            raise ValueError("BILLING_DATE_RANGE_INVALID")


def filter_records(
    records: list[dict[str, object]], page: BillingPageQuery, *, text_only: bool = False, include_text: bool = True
) -> list[dict[str, object]]:
    filters = (
        {}
        if text_only
        else {
            "project_id": page.project_id,
            "source": page.source,
            "model_id": page.model_id,
            "actor_id": page.actor_id,
            "episode_id": page.episode_id,
            "shot_id": page.shot_id,
            "status": page.status,
        }
    )
    query = (page.query or "").strip().casefold() if include_text else ""
    result: list[dict[str, object]] = []
    for record in records:
        if any(value is not None and record.get(key) != value for key, value in filters.items()):
            continue
        if not text_only and (page.date_from is not None or page.date_to is not None):
            raw = record.get("occurred_at") or record.get("updated_at")
            if not isinstance(raw, str):
                continue
            instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            if page.date_from is not None and instant < page.date_from:
                continue
            if page.date_to is not None and instant >= page.date_to:
                continue
        if query and query not in " ".join(str(value) for value in record.values()).casefold():
            continue
        result.append(record)
    return result
