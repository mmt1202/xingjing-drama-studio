package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import java.util.List;
import java.util.UUID;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/session/context")
class SessionContextController {
    private final SessionContextService service;

    SessionContextController(SessionContextService service) {
        this.service = service;
    }

    @GetMapping
    ApiEnvelope<ContextResponse> get(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        return ApiEnvelope.success(toResponse(service.get(principal)), RequestIdFilter.requestId(servletRequest));
    }

    @PutMapping("/workspace")
    ApiEnvelope<ContextResponse> selectWorkspace(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody SelectWorkspaceRequest request,
            HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        return ApiEnvelope.success(
                toResponse(service.selectWorkspace(principal, request.workspaceId(), idempotencyKey, requestId)),
                requestId);
    }

    private static ContextResponse toResponse(SessionContextService.SessionContext context) {
        return new ContextResponse(
                new UserResponse(
                        context.principal().userId(), context.principal().email(), context.principal().displayName()),
                toWorkspace(context.currentWorkspace()),
                context.workspaces().stream().map(SessionContextController::toWorkspace).toList());
    }

    private static WorkspaceResponse toWorkspace(IdentityRepository.WorkspaceAccess workspace) {
        return workspace == null
                ? null
                : new WorkspaceResponse(
                        workspace.id(),
                        workspace.name(),
                        workspace.slug(),
                        workspace.status(),
                        workspace.kind(),
                        workspace.planCode(),
                        workspace.role(),
                        workspace.lastSelectedAt(),
                        workspace.version());
    }

    record SelectWorkspaceRequest(@NotNull UUID workspaceId) {}

    record ContextResponse(UserResponse user, WorkspaceResponse currentWorkspace, List<WorkspaceResponse> workspaces) {}

    record UserResponse(UUID id, String email, String displayName) {}

    record WorkspaceResponse(
            UUID id,
            String name,
            String slug,
            String status,
            String kind,
            String planCode,
            String role,
            java.time.Instant lastSelectedAt,
            long version) {}
}
