package com.xingjing.platform.identity;

import com.xingjing.platform.core.UuidV7;
import java.time.Instant;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class WorkspaceLifecycleService {
    private final IdentityRepository repository;

    WorkspaceLifecycleService(IdentityRepository repository) {
        this.repository = repository;
    }

    @Transactional
    IdentityRepository.WorkspaceAccess createTeamWorkspace(
            SessionPrincipal principal,
            String name,
            String slug,
            String idempotencyKey,
            String requestId) {
        String key = requireIdempotencyKey(idempotencyKey);
        String normalizedName = name.trim();
        String normalizedSlug = slug.trim().toLowerCase(java.util.Locale.ROOT);
        String fingerprint = TokenHasher.sha256(normalizedName + "\u0000" + normalizedSlug);
        repository.lockWorkspaceCommand(principal.userId(), "workspace.create", key);
        IdentityRepository.WorkspaceCommandReceipt receipt =
                repository.findWorkspaceCommandReceipt(principal.userId(), "workspace.create", key);
        if (receipt != null) {
            if (!receipt.requestFingerprint().equals(fingerprint)) {
                throw new SessionContextService.IdempotencyConflictException();
            }
            return requiredWorkspace(principal.userId(), receipt.workspaceId());
        }

        UUID workspaceId = UuidV7.randomUuid();
        Instant now = Instant.now();
        try {
            repository.insertTeamWorkspace(
                    workspaceId, principal.userId(), normalizedName, normalizedSlug, now);
            repository.insertOwnerMembership(workspaceId, principal.userId(), now);
            repository.insertWorkspaceCommandReceipt(
                    principal.userId(), "workspace.create", key, fingerprint, workspaceId, now);
        } catch (DuplicateKeyException error) {
            throw new WorkspaceSlugConflictException();
        }
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                workspaceId,
                "workspace.create",
                "workspace",
                workspaceId,
                "SUCCESS",
                now);
        return requiredWorkspace(principal.userId(), workspaceId);
    }

    @Transactional
    IdentityRepository.WorkspaceAccess archiveTeamWorkspace(
            SessionPrincipal principal,
            UUID workspaceId,
            long expectedVersion,
            String idempotencyKey,
            String requestId) {
        String key = requireIdempotencyKey(idempotencyKey);
        String fingerprint = TokenHasher.sha256(workspaceId + "\u0000" + expectedVersion);
        repository.lockWorkspaceCommand(principal.userId(), "workspace.archive", key);
        IdentityRepository.WorkspaceCommandReceipt receipt =
                repository.findWorkspaceCommandReceipt(principal.userId(), "workspace.archive", key);
        if (receipt != null) {
            if (!receipt.requestFingerprint().equals(fingerprint)) {
                throw new SessionContextService.IdempotencyConflictException();
            }
            return requiredWorkspaceIncludingClosed(principal.userId(), workspaceId);
        }
        IdentityRepository.OwnedWorkspace workspace =
                repository.findOwnedWorkspaceForUpdate(principal.userId(), workspaceId);
        if (workspace == null || !"TEAM".equals(workspace.kind())) {
            throw new WorkspaceOwnerRequiredException();
        }
        if (workspace.version() != expectedVersion) {
            throw new WorkspaceVersionConflictException();
        }
        Instant now = Instant.now();
        repository.archiveWorkspace(workspaceId, expectedVersion, principal.userId(), now);
        repository.revokeWorkspaceSessions(workspaceId, now);
        repository.insertWorkspaceCommandReceipt(
                principal.userId(), "workspace.archive", key, fingerprint, workspaceId, now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                workspaceId,
                "workspace.archive",
                "workspace",
                workspaceId,
                "SUCCESS",
                now);
        return requiredWorkspaceIncludingClosed(principal.userId(), workspaceId);
    }

    @Transactional
    IdentityRepository.WorkspaceAccess transferTeamWorkspaceOwnership(
            SessionPrincipal principal,
            UUID workspaceId,
            UUID targetUserId,
            long expectedVersion,
            String idempotencyKey,
            String requestId) {
        if (principal.userId().equals(targetUserId)) {
            throw new WorkspaceOwnershipSelfTransferException();
        }
        String key = requireIdempotencyKey(idempotencyKey);
        String action = "workspace.owner.transfer";
        String fingerprint = TokenHasher.sha256(
                workspaceId + "\u0000" + targetUserId + "\u0000" + expectedVersion);
        repository.lockWorkspaceCommand(principal.userId(), action, key);
        IdentityRepository.WorkspaceCommandReceipt receipt =
                repository.findWorkspaceCommandReceipt(principal.userId(), action, key);
        if (receipt != null) {
            if (!receipt.requestFingerprint().equals(fingerprint)) {
                throw new SessionContextService.IdempotencyConflictException();
            }
            return requiredWorkspace(principal.userId(), workspaceId);
        }
        IdentityRepository.OwnedWorkspace workspace =
                repository.findOwnedWorkspaceForUpdate(principal.userId(), workspaceId);
        if (workspace == null || !"TEAM".equals(workspace.kind())) {
            throw new WorkspaceOwnerRequiredException();
        }
        if (workspace.version() != expectedVersion) {
            throw new WorkspaceVersionConflictException();
        }
        if (!repository.activeWorkspaceMemberForUpdate(workspaceId, targetUserId)) {
            throw new WorkspaceOwnershipTargetNotFoundException();
        }
        Instant now = Instant.now();
        if (!repository.transferWorkspaceOwnership(
                workspaceId, principal.userId(), targetUserId, expectedVersion, now)) {
            throw new WorkspaceVersionConflictException();
        }
        repository.insertWorkspaceCommandReceipt(
                principal.userId(), action, key, fingerprint, workspaceId, now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                workspaceId,
                action,
                "workspace_member",
                targetUserId,
                "SUCCESS",
                now);
        return requiredWorkspace(principal.userId(), workspaceId);
    }

    private IdentityRepository.WorkspaceAccess requiredWorkspace(UUID userId, UUID workspaceId) {
        IdentityRepository.WorkspaceAccess workspace = repository.findWorkspaceAccess(userId, workspaceId, false);
        if (workspace == null) {
            throw new SessionContextService.WorkspaceAccessDeniedException();
        }
        return workspace;
    }

    private IdentityRepository.WorkspaceAccess requiredWorkspaceIncludingClosed(UUID userId, UUID workspaceId) {
        IdentityRepository.WorkspaceAccess workspace = repository.findWorkspaceAccess(userId, workspaceId, true);
        if (workspace == null) {
            throw new SessionContextService.WorkspaceAccessDeniedException();
        }
        return workspace;
    }

    private static String requireIdempotencyKey(String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank() || idempotencyKey.trim().length() > 160) {
            throw new SessionContextService.IdempotencyKeyRequiredException();
        }
        return idempotencyKey.trim();
    }

    static class WorkspaceSlugConflictException extends RuntimeException {}

    static class WorkspaceOwnerRequiredException extends RuntimeException {}

    static class WorkspaceVersionConflictException extends RuntimeException {}

    static class WorkspaceOwnershipTargetNotFoundException extends RuntimeException {}

    static class WorkspaceOwnershipSelfTransferException extends RuntimeException {}
}
