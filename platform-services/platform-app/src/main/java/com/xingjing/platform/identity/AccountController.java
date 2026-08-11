package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

@RestController
@RequestMapping("/api/v1/account")
class AccountController {
    private final IdentityService identityService;

    AccountController(IdentityService identityService) {
        this.identityService = identityService;
    }

    @GetMapping("/profile")
    ApiEnvelope<ProfileResponse> profile(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        return ApiEnvelope.success(
                new ProfileResponse(
                        principal.userId(),
                        principal.email(),
                        principal.displayName(),
                        identityService.accountVersion(principal)),
                RequestIdFilter.requestId(servletRequest));
    }

    record ProfileResponse(UUID id, String email, String displayName, long version) {}

    @GetMapping("/export")
    ApiEnvelope<IdentityService.AccountExport> export(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        return ApiEnvelope.success(identityService.exportAccount(principal, requestId), requestId);
    }

    @GetMapping("/sessions")
    ApiEnvelope<java.util.List<DeviceSessionResponse>> sessions(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        var sessions = identityService.sessions(principal).stream()
                .map(session -> new DeviceSessionResponse(
                        session.id(),
                        session.deviceName(),
                        session.riskStatus(),
                        session.accessExpiresAt(),
                        session.refreshExpiresAt(),
                        session.revokedAt(),
                        session.lastSeenAt(),
                        session.createdAt(),
                        session.id().equals(principal.sessionId())))
                .toList();
        return ApiEnvelope.success(sessions, RequestIdFilter.requestId(servletRequest));
    }

    @DeleteMapping("/sessions/{sessionId}")
    ResponseEntity<Void> revokeSession(
            @AuthenticationPrincipal SessionPrincipal principal,
            @PathVariable UUID sessionId,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            HttpServletRequest servletRequest) {
        identityService.revokeSession(
                principal, sessionId, idempotencyKey, RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/sessions/revoke-others")
    ResponseEntity<Void> revokeOtherSessions(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            HttpServletRequest servletRequest) {
        identityService.revokeOtherSessions(
                principal, idempotencyKey, RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/security-events")
    ApiEnvelope<SecurityEventPageResponse> securityEvents(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestParam(defaultValue = "25") int pageSize,
            @RequestParam(required = false) String pageToken,
            @RequestParam(required = false) String result,
            @RequestParam(required = false) String deviceName,
            @RequestParam(required = false) String requestId,
            HttpServletRequest servletRequest) {
        IdentityService.SecurityEventPage page = identityService.securityEvents(
                principal, pageSize, pageToken, result, deviceName, requestId);
        var events = page.items().stream()
                .map(event -> new SecurityEventResponse(
                        event.id(),
                        event.requestId(),
                        event.result(),
                        event.reason(),
                        event.ipAddress(),
                        event.region(),
                        event.deviceName(),
                        event.occurredAt()))
                .toList();
        return ApiEnvelope.success(
                new SecurityEventPageResponse(events, page.nextPageToken()),
                RequestIdFilter.requestId(servletRequest));
    }

    @PostMapping("/cancel")
    ResponseEntity<Void> cancel(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody CancelAccountRequest request,
            HttpServletRequest servletRequest) {
        identityService.cancelAccount(
                principal,
                request.password(),
                request.confirmation(),
                request.expectedVersion(),
                idempotencyKey,
                RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/cancellation-impact")
    ApiEnvelope<IdentityRepository.AccountCancellationImpact> cancellationImpact(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        return ApiEnvelope.success(
                identityService.accountCancellationImpact(principal),
                RequestIdFilter.requestId(servletRequest));
    }

    @GetMapping("/oidc-bindings")
    ApiEnvelope<java.util.List<ExternalIdentityResponse>> oidcBindings(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        var bindings = identityService.externalIdentities(principal).stream()
                .map(binding -> new ExternalIdentityResponse(
                        binding.id(),
                        binding.providerKey(),
                        binding.subject(),
                        binding.emailAtBinding(),
                        binding.createdAt(),
                        binding.revokedAt()))
                .toList();
        return ApiEnvelope.success(bindings, RequestIdFilter.requestId(servletRequest));
    }

    @PostMapping("/oidc-bindings")
    ResponseEntity<Void> bindOidc(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            @Valid @RequestBody BindOidcRequest request,
            HttpServletRequest servletRequest) {
        identityService.bindOidcIdentity(
                principal,
                request.provider(),
                request.accessToken(),
                idempotencyKey,
                RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/oidc-bindings/{bindingId}")
    ResponseEntity<Void> revokeOidc(
            @AuthenticationPrincipal SessionPrincipal principal,
            @PathVariable UUID bindingId,
            @RequestHeader(name = "X-Step-Up-Token", required = false) String stepUpToken,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey,
            HttpServletRequest servletRequest) {
        identityService.revokeOidcIdentity(
                principal, bindingId, stepUpToken, idempotencyKey, RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/step-up")
    ApiEnvelope<StepUpResponse> stepUp(
            @AuthenticationPrincipal SessionPrincipal principal,
            @Valid @RequestBody StepUpRequest request,
            HttpServletRequest servletRequest) {
        IdentityService.StepUpToken token = identityService.createStepUp(
                principal,
                request.purpose(),
                request.password(),
                request.oidcProvider(),
                request.oidcAccessToken());
        return ApiEnvelope.success(
                new StepUpResponse(token.token(), token.expiresAt()),
                RequestIdFilter.requestId(servletRequest));
    }

    @PostMapping("/step-up/consume")
    ResponseEntity<Void> consumeStepUp(
            @AuthenticationPrincipal SessionPrincipal principal,
            @RequestHeader(name = "X-Step-Up-Token", required = false) String stepUpToken,
            @Valid @RequestBody ConsumeStepUpRequest request) {
        if (!java.util.Set.of("api_key.create", "api_key.rotate", "api_key.revoke")
                .contains(request.purpose())) {
            throw new IdentityService.StepUpRequiredException();
        }
        identityService.consumeStepUp(principal, stepUpToken, request.purpose());
        return ResponseEntity.noContent().build();
    }

    record DeviceSessionResponse(
            UUID id,
            String deviceName,
            String riskStatus,
            java.time.Instant accessExpiresAt,
            java.time.Instant refreshExpiresAt,
            java.time.Instant revokedAt,
            java.time.Instant lastSeenAt,
            java.time.Instant createdAt,
            boolean current) {}

    record SecurityEventResponse(
            UUID id,
            String requestId,
            String result,
            String reason,
            String ipAddress,
            String region,
            String deviceName,
            java.time.Instant occurredAt) {}

    record SecurityEventPageResponse(
            java.util.List<SecurityEventResponse> items,
            String nextPageToken) {}

    record CancelAccountRequest(
            @NotBlank @Size(max = 128) String password,
            @NotBlank @Size(max = 16) String confirmation,
            @jakarta.validation.constraints.PositiveOrZero long expectedVersion) {}

    record ExternalIdentityResponse(
            UUID id,
            String provider,
            String subject,
            String email,
            java.time.Instant createdAt,
            java.time.Instant revokedAt) {}

    record BindOidcRequest(
            @NotBlank @Size(max = 80) String provider,
            @NotBlank @Size(max = 4096) String accessToken) {}

    record StepUpRequest(
            @NotBlank @Size(max = 120) String purpose,
            @Size(max = 128) String password,
            @Size(max = 80) String oidcProvider,
            @Size(max = 4096) String oidcAccessToken) {}

    record StepUpResponse(String token, java.time.Instant expiresAt) {}

    record ConsumeStepUpRequest(@NotBlank @Size(max = 120) String purpose) {}
}
