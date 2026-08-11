"""M07 音频、字幕和对口型的 PostgreSQL 持久化适配器。

此包刻意不保存媒体二进制数据；所有媒体字段只保存对象存储引用与校验摘要。
"""

from server.xingjing_audio_persistence.models import metadata
from server.xingjing_audio_persistence.repository import (
    AudioBillingError,
    AudioPersistenceConflict,
    AudioPersistenceNotFound,
    AudioPostgresRepository,
    IdempotencyConflict,
    VersionConflict,
)

__all__ = [
    "AudioBillingError",
    "AudioPersistenceConflict",
    "AudioPersistenceNotFound",
    "AudioPostgresRepository",
    "IdempotencyConflict",
    "VersionConflict",
    "metadata",
]
