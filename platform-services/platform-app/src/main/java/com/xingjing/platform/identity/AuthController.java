package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/auth")
class AuthController {
    private final IdentityService identityService;
    private final LoginSecurityEventService loginSecurityEventService;

    AuthController(IdentityService identityService, LoginSecurityEventService loginSecurityEventService) {
        this.identityService = identityService;
        this.loginSecurityEventService = loginSecurityEventService;
    }

    @PostMapping("/register")
    ResponseEntity<ApiEnvelope<RegistrationResponse>> register(
            @Valid @RequestBody RegistrationRequest request, HttpServletRequest servletRequest) {
        IdentityService.Registration registration = identityService.register(
                request.email(), request.password(), request.displayName());
        RegistrationResponse body = new RegistrationResponse(
                new UserResponse(registration.userId(), registration.email(), registration.displayName()),
                new WorkspaceResponse(registration.workspaceId(), registration.workspaceName(), "OWNER"));
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(ApiEnvelope.success(body, RequestIdFilter.requestId(servletRequest)));
    }

    @PostMapping("/login")
    ApiEnvelope<LoginResponse> login(@Valid @RequestBody LoginRequest request, HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        IdentityService.LoginResult login;
        try {
            login = identityService.login(request.email(), request.password(), request.deviceName());
        } catch (IdentityService.InvalidCredentialsException error) {
            loginSecurityEventService.record(
                    requestId, request.email(), error.userId(), false, "INVALID_CREDENTIALS",
                    servletRequest.getRemoteAddr(), request.deviceName());
            throw error;
        }
        loginSecurityEventService.record(
                requestId, request.email(), login.userId(), true, "AUTHENTICATED",
                servletRequest.getRemoteAddr(), request.deviceName());
        IdentityService.SessionTokens tokens = login.tokens();
        return ApiEnvelope.success(
                new LoginResponse(
                        tokens.accessToken(), tokens.refreshToken(), tokens.accessExpiresAt(), tokens.refreshExpiresAt()),
                requestId);
    }

    @PostMapping("/refresh")
    ApiEnvelope<LoginResponse> refresh(
            @Valid @RequestBody RefreshRequest request, HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        IdentityService.SessionTokens tokens = identityService.refresh(request.refreshToken(), requestId);
        return ApiEnvelope.success(
                new LoginResponse(
                        tokens.accessToken(), tokens.refreshToken(), tokens.accessExpiresAt(), tokens.refreshExpiresAt()),
                requestId);
    }

    @PostMapping("/oidc")
    ApiEnvelope<LoginResponse> oidc(
            @Valid @RequestBody OidcLoginRequest request, HttpServletRequest servletRequest) {
        String requestId = RequestIdFilter.requestId(servletRequest);
        IdentityService.LoginResult login = identityService.loginWithOidc(
                request.provider(), request.accessToken(), request.deviceName(), requestId);
        IdentityService.SessionTokens tokens = login.tokens();
        return ApiEnvelope.success(
                new LoginResponse(
                        tokens.accessToken(), tokens.refreshToken(), tokens.accessExpiresAt(), tokens.refreshExpiresAt()),
                requestId);
    }

    @PostMapping("/logout")
    ResponseEntity<Void> logout(
            @AuthenticationPrincipal SessionPrincipal principal, HttpServletRequest servletRequest) {
        identityService.logout(principal, RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/password-reset/request")
    ResponseEntity<Void> requestPasswordReset(
            @Valid @RequestBody PasswordResetRequest request,
            HttpServletRequest servletRequest) {
        identityService.requestPasswordReset(
                request.email(),
                RequestIdFilter.requestId(servletRequest),
                servletRequest.getRemoteAddr());
        return ResponseEntity.accepted().build();
    }

    @PostMapping("/password-reset/confirm")
    ResponseEntity<Void> confirmPasswordReset(
            @Valid @RequestBody PasswordResetConfirm request,
            HttpServletRequest servletRequest) {
        identityService.resetPassword(
                request.token(), request.newPassword(), RequestIdFilter.requestId(servletRequest));
        return ResponseEntity.noContent().build();
    }

    record RegistrationRequest(
            @Email @NotBlank @Size(max = 320) String email,
            @NotBlank @Size(min = 12, max = 128) String password,
            @NotBlank @Size(max = 120) String displayName) {}

    record RegistrationResponse(UserResponse user, WorkspaceResponse workspace) {}

    record LoginRequest(
            @Email @NotBlank @Size(max = 320) String email,
            @NotBlank @Size(min = 1, max = 128) String password,
            @Size(max = 160) String deviceName) {}

    record LoginResponse(
            String accessToken, String refreshToken, java.time.Instant accessExpiresAt, java.time.Instant refreshExpiresAt) {}

    record RefreshRequest(@NotBlank @Size(max = 512) String refreshToken) {}

    record OidcLoginRequest(
            @NotBlank @Size(max = 80) String provider,
            @NotBlank @Size(max = 4096) String accessToken,
            @Size(max = 160) String deviceName) {}

    record PasswordResetRequest(@Email @NotBlank @Size(max = 320) String email) {}

    record PasswordResetConfirm(
            @NotBlank @Size(max = 512) String token,
            @NotBlank @Size(min = 12, max = 128) String newPassword) {}

    record UserResponse(UUID id, String email, String displayName) {}

    record WorkspaceResponse(UUID id, String name, String role) {}
}
