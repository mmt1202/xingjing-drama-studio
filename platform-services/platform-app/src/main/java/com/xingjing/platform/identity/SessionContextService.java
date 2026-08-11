package com.xingjing.platform.identity;

import com.xingjing.platform.core.UuidV7;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class SessionContextService {
    private final IdentityRepository repository;

    SessionContextService(IdentityRepository repository) {
        this.repository = repository;
    }

    SessionContext get(SessionPrincipal principal) {
        List<IdentityRepository.WorkspaceAccess> workspaces = repository.findAccessibleWorkspaces(principal.userId());
        UUID currentWorkspaceId = repository.findCurrentWorkspaceId(principal.sessionId());
        IdentityRepository.WorkspaceAccess currentWorkspace = workspaces.stream()
                .filter(workspace -> workspace.id().equals(currentWorkspaceId))
                .findFirst()
                .orElse(null);
        return new SessionContext(principal, currentWorkspace, workspaces);
    }

    @Transactional
    SessionContext selectWorkspace(
            SessionPrincipal principal, UUID workspaceId, String idempotencyKey, String requestId) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new IdempotencyKeyRequiredException();
        }
        String normalizedKey = idempotencyKey.trim();
        if (normalizedKey.length() > 160) {
            throw new InvalidIdempotencyKeyException();
        }

        IdentityRepository.WorkspaceSelection previous =
                repository.findWorkspaceSelection(principal.sessionId(), normalizedKey);
        if (previous != null) {
            if (!previous.workspaceId().equals(workspaceId)) {
                throw new IdempotencyConflictException();
            }
            return get(principal);
        }

        if (!repository.hasActiveWorkspaceMembership(principal.userId(), workspaceId)) {
            throw new WorkspaceAccessDeniedException();
        }

        Instant now = Instant.now();
        repository.selectWorkspace(principal.sessionId(), normalizedKey, workspaceId, requestId, now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                workspaceId,
                "session.workspace.select",
                "workspace",
                workspaceId,
                "SUCCESS",
                now);
        return get(principal);
    }

    record SessionContext(
            SessionPrincipal principal,
            IdentityRepository.WorkspaceAccess currentWorkspace,
            List<IdentityRepository.WorkspaceAccess> workspaces) {}

    static class IdempotencyKeyRequiredException extends RuntimeException {}

    static class InvalidIdempotencyKeyException extends RuntimeException {}

    static class IdempotencyConflictException extends RuntimeException {}

    static class WorkspaceAccessDeniedException extends RuntimeException {}
}
