package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiErrorEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice(basePackages = "com.xingjing.platform.identity")
class IdentityExceptionHandler {
    @ExceptionHandler(IdentityService.DuplicateEmailException.class)
    ResponseEntity<ApiErrorEnvelope> duplicateEmail(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "ACCOUNT_ALREADY_EXISTS", "账号无法注册", request);
    }

    @ExceptionHandler(IdentityService.InvalidCredentialsException.class)
    ResponseEntity<ApiErrorEnvelope> invalidCredentials(HttpServletRequest request) {
        return error(HttpStatus.UNAUTHORIZED, "INVALID_CREDENTIALS", "账号或凭证无效", request);
    }

    @ExceptionHandler(IdentityService.InvalidRefreshTokenException.class)
    ResponseEntity<ApiErrorEnvelope> invalidRefreshToken(HttpServletRequest request) {
        return error(HttpStatus.UNAUTHORIZED, "INVALID_REFRESH_TOKEN", "刷新凭证无效或已过期", request);
    }

    @ExceptionHandler(IdentityService.SessionNotFoundException.class)
    ResponseEntity<ApiErrorEnvelope> sessionNotFound(HttpServletRequest request) {
        return error(HttpStatus.NOT_FOUND, "SESSION_NOT_FOUND", "设备会话不存在或已失效", request);
    }

    @ExceptionHandler(IdentityService.InvalidPasswordResetTokenException.class)
    ResponseEntity<ApiErrorEnvelope> invalidPasswordResetToken(HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_PASSWORD_RESET_TOKEN", "重置凭证无效或已过期", request);
    }

    @ExceptionHandler(IdentityService.InvalidSecurityEventQueryException.class)
    ResponseEntity<ApiErrorEnvelope> invalidSecurityEventQuery(HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_SECURITY_EVENT_QUERY", "安全事件筛选或分页参数无效", request);
    }

    @ExceptionHandler(IdentityService.AccountCancellationConfirmationException.class)
    ResponseEntity<ApiErrorEnvelope> accountCancellationConfirmation(HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "ACCOUNT_CANCELLATION_CONFIRMATION_REQUIRED", "请输入 CANCEL 确认注销", request);
    }

    @ExceptionHandler(IdentityService.AccountVersionConflictException.class)
    ResponseEntity<ApiErrorEnvelope> accountVersionConflict(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "VERSION_CONFLICT", "账号状态已发生变化，请刷新后重试", request);
    }

    @ExceptionHandler(IdentityService.OwnedTeamWorkspaceExistsException.class)
    ResponseEntity<ApiErrorEnvelope> ownedTeamWorkspaceExists(HttpServletRequest request) {
        return error(
                HttpStatus.CONFLICT,
                "OWNED_TEAM_WORKSPACE_EXISTS",
                "请先转移或归档你拥有的团队工作区",
                request);
    }

    @ExceptionHandler(OidcIdentityService.OidcUnavailableException.class)
    ResponseEntity<ApiErrorEnvelope> oidcUnavailable(HttpServletRequest request) {
        return error(HttpStatus.SERVICE_UNAVAILABLE, "OIDC_PROVIDER_UNAVAILABLE", "企业身份提供方尚未配置", request);
    }

    @ExceptionHandler(OidcIdentityService.InvalidOidcIdentityException.class)
    ResponseEntity<ApiErrorEnvelope> invalidOidcIdentity(HttpServletRequest request) {
        return error(HttpStatus.UNAUTHORIZED, "INVALID_OIDC_IDENTITY", "企业身份凭证无效", request);
    }

    @ExceptionHandler(IdentityService.OidcBindingRequiredException.class)
    ResponseEntity<ApiErrorEnvelope> oidcBindingRequired(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "OIDC_BINDING_REQUIRED", "该邮箱已有账号，请登录后绑定企业身份", request);
    }

    @ExceptionHandler({
        IdentityService.OidcEmailMismatchException.class,
        IdentityService.OidcIdentityConflictException.class
    })
    ResponseEntity<ApiErrorEnvelope> oidcIdentityConflict(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "OIDC_IDENTITY_CONFLICT", "企业身份与当前账号不一致", request);
    }

    @ExceptionHandler(IdentityService.OidcIdentityNotFoundException.class)
    ResponseEntity<ApiErrorEnvelope> oidcIdentityNotFound(HttpServletRequest request) {
        return error(HttpStatus.NOT_FOUND, "OIDC_IDENTITY_NOT_FOUND", "企业身份绑定不存在", request);
    }

    @ExceptionHandler(IdentityService.StepUpRequiredException.class)
    ResponseEntity<ApiErrorEnvelope> stepUpRequired(HttpServletRequest request) {
        return error(HttpStatus.FORBIDDEN, "STEP_UP_REQUIRED", "该操作需要重新验证身份", request);
    }

    @ExceptionHandler(WorkspaceLifecycleService.WorkspaceSlugConflictException.class)
    ResponseEntity<ApiErrorEnvelope> workspaceSlugConflict(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "WORKSPACE_SLUG_CONFLICT", "工作区标识已被使用", request);
    }

    @ExceptionHandler(WorkspaceLifecycleService.WorkspaceOwnerRequiredException.class)
    ResponseEntity<ApiErrorEnvelope> workspaceOwnerRequired(HttpServletRequest request) {
        return error(HttpStatus.FORBIDDEN, "WORKSPACE_OWNER_REQUIRED", "仅团队工作区所有者可执行此操作", request);
    }

    @ExceptionHandler(WorkspaceLifecycleService.WorkspaceVersionConflictException.class)
    ResponseEntity<ApiErrorEnvelope> workspaceVersionConflict(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "VERSION_CONFLICT", "工作区已被其他操作更新", request);
    }

    @ExceptionHandler(WorkspaceLifecycleService.WorkspaceOwnershipTargetNotFoundException.class)
    ResponseEntity<ApiErrorEnvelope> workspaceOwnershipTargetNotFound(HttpServletRequest request) {
        return error(HttpStatus.NOT_FOUND, "WORKSPACE_OWNERSHIP_TARGET_NOT_FOUND", "目标用户不是当前工作区的活跃成员", request);
    }

    @ExceptionHandler(WorkspaceLifecycleService.WorkspaceOwnershipSelfTransferException.class)
    ResponseEntity<ApiErrorEnvelope> workspaceOwnershipSelfTransfer(HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "WORKSPACE_OWNERSHIP_SELF_TRANSFER", "不能将所有权移交给自己", request);
    }

    @ExceptionHandler(SessionContextService.IdempotencyKeyRequiredException.class)
    ResponseEntity<ApiErrorEnvelope> idempotencyKeyRequired(HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key", request);
    }

    @ExceptionHandler(SessionContextService.InvalidIdempotencyKeyException.class)
    ResponseEntity<ApiErrorEnvelope> invalidIdempotencyKey(HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 格式无效", request);
    }

    @ExceptionHandler(SessionContextService.IdempotencyConflictException.class)
    ResponseEntity<ApiErrorEnvelope> idempotencyConflict(HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求", request);
    }

    @ExceptionHandler(SessionContextService.WorkspaceAccessDeniedException.class)
    ResponseEntity<ApiErrorEnvelope> workspaceAccessDenied(HttpServletRequest request) {
        return error(HttpStatus.FORBIDDEN, "WORKSPACE_ACCESS_DENIED", "无权访问该工作区", request);
    }

    private static ResponseEntity<ApiErrorEnvelope> error(
            HttpStatus status, String code, String message, HttpServletRequest request) {
        String requestId = RequestIdFilter.requestId(request);
        return ResponseEntity.status(status).body(ApiErrorEnvelope.of(code, message, false, requestId));
    }
}
