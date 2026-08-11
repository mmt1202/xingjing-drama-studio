"""M10 团队领域的 PostgreSQL SQLAlchemy 持久化适配器。"""

from .repository import SqlAlchemyTeamRepository, TeamPersistenceBase, TeamPersistenceScope

__all__ = ["SqlAlchemyTeamRepository", "TeamPersistenceBase", "TeamPersistenceScope"]
