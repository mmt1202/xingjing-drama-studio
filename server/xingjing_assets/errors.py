class AssetDomainError(Exception):
    """资产领域错误基类。"""


class AssetNotFound(AssetDomainError):
    pass


class VersionConflict(AssetDomainError):
    pass


class CrossProjectReuseDenied(AssetDomainError):
    pass
