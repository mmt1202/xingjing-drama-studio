package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/workspaces")
class WorkspaceController {
    private final WorkspaceLifecycleService service;

    WorkspaceController(WorkspaceLifecycleService service) {
        this.service = service;
    }

    @PostMapping
    ResponseEntity<ApiEnvelope<WorkspaceResponse>> create(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody CreateWorkspaceRequest request,
            HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        IdentityRepository.WorkspaceAccess workspace =
                service.createTeamWorkspace(principal, request.name(), request.slug(), idempotencyKey, requestId);
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(ApiEnvelope.success(toResponse(workspace), requestId));
    }

    @PostMapping("/{workspaceId}/archive")
    ApiEnvelope<WorkspaceResponse> archive(
            @AuthenticationPrincipal SessionPrincipal principal,
            @PathVariable UUID workspaceId,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody ArchiveWorkspaceRequest request,
            HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        return ApiEnvelope.success(
                toResponse(service.archiveTeamWorkspace(
                        principal, workspaceId, request.expectedVersion(), idempotencyKey, requestId)),
                requestId);
    }

    @PostMapping("/{workspaceId}/transfer-ownership")
    ApiEnvelope<WorkspaceResponse> transferOwnership(
            @AuthenticationPrincipal SessionPrincipal principal,
            @PathVariable UUID workspaceId,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody TransferWorkspaceOwnershipRequest request,
            HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        return ApiEnvelope.success(
                toResponse(service.transferTeamWorkspaceOwnership(
                        principal,
                        workspaceId,
                        request.targetUserId(),
                        request.expectedVersion(),
                        idempotencyKey,
                        requestId)),
                requestId);
    }

    private static WorkspaceResponse toResponse(IdentityRepository.WorkspaceAccess workspace) {
        return new WorkspaceResponse(
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

    record CreateWorkspaceRequest(
            @NotBlank @Size(max = 160) String name,
            @NotBlank @Size(max = 120)
                    @Pattern(regexp = "[a-z0-9]+(?:-[a-z0-9]+)*")
                    String slug) {}

    record ArchiveWorkspaceRequest(@NotNull Long expectedVersion) {}

    record TransferWorkspaceOwnershipRequest(
            @NotNull UUID targetUserId,
            @NotNull Long expectedVersion) {}

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
