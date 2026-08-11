from __future__ import annotations

WORKSPACE_OWNER_PERMISSIONS = frozenset(
    {
        "workspace.view",
        "workspace.manage",
        "workspace.member.view",
        "workspace.member.manage",
        "workspace.enterprise.view",
        "workspace.enterprise.manage",
        "workspace.audit.view",
        "workspace.audit.manage",
        "template.view",
        "template.manage",
        "community.view",
        "community.manage",
        "project.view",
        "project.manage",
        "script.view",
        "script.manage",
        "asset.view",
        "asset.manage",
        "generation.view",
        "generation.manage",
        "audio.view",
        "audio.manage",
        "shot.view",
        "shot.manage",
        "final.view",
        "final.manage",
        "compliance.view",
        "compliance.manage",
        "export.view",
        "export.manage",
        "commercial.view",
        "commercial.manage",
        "review.view",
        "review.manage",
        "billing.view",
        "billing.manage",
        "settings.view",
        "settings.manage",
    }
)

WORKSPACE_ADMIN_PERMISSIONS = WORKSPACE_OWNER_PERMISSIONS - {"workspace.manage"}

WORKSPACE_MEMBER_PERMISSIONS = frozenset(
    {
        "workspace.view",
        "workspace.member.view",
        "workspace.audit.view",
        "template.view",
        "community.view",
        "project.view",
        "script.view",
        "asset.view",
        "generation.view",
        "audio.view",
        "shot.view",
        "final.view",
        "compliance.view",
        "export.view",
        "commercial.view",
        "review.view",
    }
)

PLATFORM_ADMIN_PERMISSIONS = WORKSPACE_OWNER_PERMISSIONS | frozenset(
    {
        "admin.business.view",
        "admin.business.manage",
        "admin.commercial.view",
        "admin.commercial.manage",
        "admin.finance.view",
        "admin.finance.manage",
        "admin.finance.approve",
        "admin.model.view",
        "admin.model.manage",
        "admin.compliance.view",
        "admin.compliance.manage",
        "admin.review.view",
        "admin.review.manage",
        "admin.security.view",
        "admin.security.manage",
        "admin.ops.view",
        "admin.ops.manage",
        "admin.notification.view",
        "admin.notification.manage",
        "admin.support.view",
        "admin.support.manage",
        "admin.api.view",
        "admin.api.manage",
    }
)

DEFAULT_ROLE_PERMISSIONS = {
    "owner": WORKSPACE_OWNER_PERMISSIONS,
    "admin": WORKSPACE_ADMIN_PERMISSIONS,
    "member": WORKSPACE_MEMBER_PERMISSIONS,
}


def permissions_for_identity_role(role: str) -> frozenset[str]:
    normalized = role.strip().upper()
    if normalized == "PLATFORM_ADMIN":
        return PLATFORM_ADMIN_PERMISSIONS
    return DEFAULT_ROLE_PERMISSIONS.get(normalized.casefold(), WORKSPACE_MEMBER_PERMISSIONS)
