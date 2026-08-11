"""M12 客户审片 PostgreSQL 持久化适配器。"""

from .models import ReviewPersistenceBase
from .repository import ReviewPersistenceScope, SqlAlchemyReviewStore

__all__ = ["ReviewPersistenceBase", "ReviewPersistenceScope", "SqlAlchemyReviewStore"]
