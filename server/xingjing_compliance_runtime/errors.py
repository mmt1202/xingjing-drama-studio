class RuntimeConfigurationError(ValueError):
    """运行时未获得必需的权威依赖。"""


class AuthorityDataMissing(RuntimeError):
    """无法从权威数据源读取正式导出所需的事实。"""


class ComplianceRuntimeUnavailable(RuntimeError):
    """生产组合、身份解析或 PostgreSQL 依赖不可用时的失败关闭信号。"""


class CompliancePermissionDenied(PermissionError):
    """可信会话缺少 M09 所需权限时的拒绝信号。"""


class ComplianceProjectScopeDenied(PermissionError):
    """项目不属于当前可信工作区，或项目不存在时的拒绝信号。"""
