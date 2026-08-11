package com.xingjing.platform.identity;

import com.xingjing.platform.core.UuidV7;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class IdentityService {
    private static final int MAX_FAILED_LOGINS = 5;
    private static final String DUMMY_PASSWORD_HASH =
            "$2a$10$7EqJtq98hPqEX7fNZaFWoO5B5uFfZCqR6KXQK6mVjY.MN7yYJQe9G";
    private final IdentityRepository repository;
    private final PasswordEncoder passwordEncoder;
    private final PasswordResetDeliveryService passwordResetDeliveryService;
    private final OidcIdentityService oidcIdentityService;

    IdentityService(
            IdentityRepository repository,
            PasswordEncoder passwordEncoder,
            PasswordResetDeliveryService passwordResetDeliveryService,
            OidcIdentityService oidcIdentityService) {
        this.repository = repository;
        this.passwordEncoder = passwordEncoder;
        this.passwordResetDeliveryService = passwordResetDeliveryService;
        this.oidcIdentityService = oidcIdentityService;
    }

    @Transactional
    Registration register(String email, String password, String displayName) {
        String normalizedEmail = email.trim().toLowerCase(Locale.ROOT);
        if (repository.emailExists(normalizedEmail)) {
            throw new DuplicateEmailException();
        }

        UUID userId = UuidV7.randomUuid();
        UUID workspaceId = UuidV7.randomUuid();
        Instant now = Instant.now();
        String workspaceName = displayName.trim() + " 的工作区";
        try {
            repository.insertUser(userId, normalizedEmail, displayName.trim(), now);
            repository.insertCredential(
                    UuidV7.randomUuid(), userId, TokenHasher.sha256(normalizedEmail), passwordEncoder.encode(password), now);
            repository.insertWorkspace(workspaceId, userId, workspaceName, "personal-" + userId, now);
            repository.insertOwnerMembership(workspaceId, userId, now);
        } catch (DuplicateKeyException exception) {
            throw new DuplicateEmailException();
        }
        return new Registration(userId, normalizedEmail, displayName.trim(), workspaceId, workspaceName);
    }

    @Transactional(noRollbackFor = InvalidCredentialsException.class)
    LoginResult login(String email, String password, String deviceName) {
        String normalizedEmail = email.trim().toLowerCase(Locale.ROOT);
        Instant now = Instant.now();
        IdentityRepository.CredentialState credential =
                repository.findCredentialStateForUpdate(TokenHasher.sha256(normalizedEmail));
        if (credential == null) {
            passwordEncoder.matches(password, DUMMY_PASSWORD_HASH);
            throw new InvalidCredentialsException();
        }
        if (credential.lockedUntil() != null && credential.lockedUntil().isAfter(now)) {
            passwordEncoder.matches(password, credential.passwordHash());
            throw new InvalidCredentialsException(credential.userId());
        }
        if (!passwordEncoder.matches(password, credential.passwordHash())) {
            int failures = credential.failedCount() + 1;
            Instant lockedUntil = failures >= MAX_FAILED_LOGINS
                    ? now.plus(15, ChronoUnit.MINUTES)
                    : null;
            repository.recordFailedLogin(credential.credentialId(), failures, lockedUntil, now);
            throw new InvalidCredentialsException(credential.userId());
        }
        String riskStatus = credential.failedCount() >= 3 ? "SUSPICIOUS" : "NORMAL";
        repository.clearFailedLogin(credential.credentialId(), now);
        String accessToken = TokenHasher.newToken();
        String refreshToken = TokenHasher.newToken();
        Instant accessExpiresAt = now.plus(15, ChronoUnit.MINUTES);
        Instant refreshExpiresAt = now.plus(30, ChronoUnit.DAYS);
        repository.insertSession(
                UuidV7.randomUuid(),
                credential.userId(),
                TokenHasher.sha256(accessToken),
                TokenHasher.sha256(refreshToken),
                deviceName == null || deviceName.isBlank() ? "unknown" : deviceName.trim(),
                riskStatus,
                accessExpiresAt,
                refreshExpiresAt,
                now);
        return new LoginResult(
                credential.userId(),
                new SessionTokens(accessToken, refreshToken, accessExpiresAt, refreshExpiresAt));
    }

    @Transactional
    LoginResult loginWithOidc(String provider, String oidcAccessToken, String deviceName, String requestId) {
        OidcIdentityService.OidcIdentity identity = oidcIdentityService.verify(provider, oidcAccessToken);
        IdentityRepository.UserIdentity user =
                repository.findUserByExternalIdentity(identity.providerKey(), identity.subject());
        Instant now = Instant.now();
        if (user == null) {
            IdentityRepository.UserIdentity emailOwner = repository.findUserByEmail(identity.email());
            if (emailOwner != null) {
                throw new OidcBindingRequiredException();
            }
            UUID userId = UuidV7.randomUuid();
            UUID workspaceId = UuidV7.randomUuid();
            repository.insertUser(userId, identity.email(), identity.displayName(), now);
            repository.insertWorkspace(
                    workspaceId, userId, identity.displayName() + " 的工作区", "personal-" + userId, now);
            repository.insertOwnerMembership(workspaceId, userId, now);
            repository.insertExternalIdentity(
                    UuidV7.randomUuid(),
                    userId,
                    identity.providerKey(),
                    identity.subject(),
                    identity.email(),
                    userId,
                    now);
            user = new IdentityRepository.UserIdentity(userId, identity.email());
        }
        LoginResult result = createSession(user.id(), deviceName, "NORMAL", now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                user.id(),
                null,
                "identity.oidc.login",
                "user",
                user.id(),
                "SUCCESS",
                now);
        return result;
    }

    @Transactional
    void bindOidcIdentity(
            SessionPrincipal principal,
            String provider,
            String oidcAccessToken,
            String idempotencyKey,
            String requestId) {
        String key = requireIdempotencyKey(idempotencyKey);
        OidcIdentityService.OidcIdentity identity = oidcIdentityService.verify(provider, oidcAccessToken);
        if (!principal.email().equalsIgnoreCase(identity.email())) {
            throw new OidcEmailMismatchException();
        }
        IdentityRepository.UserIdentity existing =
                repository.findUserByExternalIdentity(identity.providerKey(), identity.subject());
        if (existing != null && !existing.id().equals(principal.userId())) {
            throw new OidcIdentityConflictException();
        }
        String fingerprint = TokenHasher.sha256(identity.providerKey() + "\u0000" + identity.subject());
        repository.lockAccountCommand(principal.userId(), "identity.oidc.bind", key);
        IdentityRepository.AccountCommandReceipt receipt =
                repository.findAccountCommandReceipt(principal.userId(), "identity.oidc.bind", key);
        if (receipt != null) {
            assertIdempotencyFingerprint(receipt, fingerprint);
            return;
        }
        Instant now = Instant.now();
        if (existing == null) {
            repository.insertExternalIdentity(
                    UuidV7.randomUuid(),
                    principal.userId(),
                    identity.providerKey(),
                    identity.subject(),
                    identity.email(),
                    principal.userId(),
                    now);
            repository.insertAuditLog(
                    UuidV7.randomUuid(),
                    requestId,
                    principal.userId(),
                    repository.findCurrentWorkspaceId(principal.sessionId()),
                    "identity.oidc.bind",
                    "user",
                    principal.userId(),
                    "SUCCESS",
                    now);
        }
        repository.insertAccountCommandReceipt(
                principal.userId(), "identity.oidc.bind", key, fingerprint, now);
    }

    java.util.List<IdentityRepository.ExternalIdentity> externalIdentities(SessionPrincipal principal) {
        return repository.findExternalIdentities(principal.userId());
    }

    @Transactional
    void revokeOidcIdentity(
            SessionPrincipal principal,
            UUID bindingId,
            String stepUpToken,
            String idempotencyKey,
            String requestId) {
        String key = requireIdempotencyKey(idempotencyKey);
        String fingerprint = TokenHasher.sha256(bindingId.toString());
        repository.lockAccountCommand(principal.userId(), "identity.oidc.revoke", key);
        IdentityRepository.AccountCommandReceipt receipt =
                repository.findAccountCommandReceipt(principal.userId(), "identity.oidc.revoke", key);
        if (receipt != null) {
            assertIdempotencyFingerprint(receipt, fingerprint);
            return;
        }
        consumeStepUp(principal, stepUpToken, "identity.oidc.revoke");
        Instant now = Instant.now();
        if (!repository.revokeExternalIdentity(principal.userId(), bindingId, now)) {
            throw new OidcIdentityNotFoundException();
        }
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                repository.findCurrentWorkspaceId(principal.sessionId()),
                "identity.oidc.revoke",
                "external_identity",
                bindingId,
                "SUCCESS",
                now);
        repository.insertAccountCommandReceipt(
                principal.userId(), "identity.oidc.revoke", key, fingerprint, now);
    }

    private static String requireIdempotencyKey(String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new SessionContextService.IdempotencyKeyRequiredException();
        }
        String key = idempotencyKey.trim();
        if (key.length() > 160) throw new SessionContextService.InvalidIdempotencyKeyException();
        return key;
    }

    private static void assertIdempotencyFingerprint(
            IdentityRepository.AccountCommandReceipt receipt, String fingerprint) {
        if (!receipt.requestFingerprint().equals(fingerprint)) {
            throw new SessionContextService.IdempotencyConflictException();
        }
    }

    @Transactional
    StepUpToken createStepUp(
            SessionPrincipal principal,
            String purpose,
            String password,
            String oidcProvider,
            String oidcAccessToken) {
        boolean verified = false;
        String passwordHash = repository.findPasswordHashForUserForUpdate(principal.userId());
        if (passwordHash != null && password != null && !password.isBlank()) {
            verified = passwordEncoder.matches(password, passwordHash);
        } else if (oidcProvider != null && oidcAccessToken != null) {
            OidcIdentityService.OidcIdentity identity =
                    oidcIdentityService.verify(oidcProvider, oidcAccessToken);
            verified = principal.email().equalsIgnoreCase(identity.email());
        }
        if (!verified) {
            throw new InvalidCredentialsException(principal.userId());
        }
        Instant now = Instant.now();
        Instant expiresAt = now.plus(5, ChronoUnit.MINUTES);
        String token = TokenHasher.newToken();
        repository.insertStepUpChallenge(
                UuidV7.randomUuid(),
                principal.userId(),
                principal.sessionId(),
                TokenHasher.sha256(token),
                purpose,
                expiresAt,
                now);
        return new StepUpToken(token, expiresAt);
    }

    @Transactional
    void consumeStepUp(SessionPrincipal principal, String token, String purpose) {
        if (token == null
                || token.isBlank()
                || !repository.consumeStepUpChallenge(
                        principal.userId(),
                        principal.sessionId(),
                        TokenHasher.sha256(token),
                        purpose,
                        Instant.now())) {
            throw new StepUpRequiredException();
        }
    }

    private LoginResult createSession(UUID userId, String deviceName, String riskStatus, Instant now) {
        String accessToken = TokenHasher.newToken();
        String refreshToken = TokenHasher.newToken();
        Instant accessExpiresAt = now.plus(15, ChronoUnit.MINUTES);
        Instant refreshExpiresAt = now.plus(30, ChronoUnit.DAYS);
        repository.insertSession(
                UuidV7.randomUuid(),
                userId,
                TokenHasher.sha256(accessToken),
                TokenHasher.sha256(refreshToken),
                deviceName == null || deviceName.isBlank() ? "unknown" : deviceName.trim(),
                riskStatus,
                accessExpiresAt,
                refreshExpiresAt,
                now);
        return new LoginResult(
                userId, new SessionTokens(accessToken, refreshToken, accessExpiresAt, refreshExpiresAt));
    }

    @Transactional
    SessionTokens refresh(String refreshToken, String requestId) {
        IdentityRepository.RefreshSession current =
                repository.findActiveSessionByRefreshTokenHashForUpdate(TokenHasher.sha256(refreshToken));
        if (current == null) {
            throw new InvalidRefreshTokenException();
        }

        Instant now = Instant.now();
        UUID replacementId = UuidV7.randomUuid();
        String accessToken = TokenHasher.newToken();
        String nextRefreshToken = TokenHasher.newToken();
        Instant accessExpiresAt = now.plus(15, ChronoUnit.MINUTES);
        Instant refreshExpiresAt = now.plus(30, ChronoUnit.DAYS);
        repository.insertRotatedSession(
                replacementId,
                current.userId(),
                TokenHasher.sha256(accessToken),
                TokenHasher.sha256(nextRefreshToken),
                current.deviceName(),
                current.riskStatus(),
                accessExpiresAt,
                refreshExpiresAt,
                current.currentWorkspaceId(),
                now);
        repository.revokeReplacedSession(current.id(), replacementId, now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                current.userId(),
                current.currentWorkspaceId(),
                "session.refresh",
                "login_session",
                current.id(),
                "SUCCESS",
                now);
        return new SessionTokens(accessToken, nextRefreshToken, accessExpiresAt, refreshExpiresAt);
    }

    @Transactional
    void logout(SessionPrincipal principal, String requestId) {
        Instant now = Instant.now();
        if (repository.revokeSession(principal.userId(), principal.sessionId(), "LOGOUT", now)) {
            repository.insertAuditLog(
                    UuidV7.randomUuid(),
                    requestId,
                    principal.userId(),
                    repository.findCurrentWorkspaceId(principal.sessionId()),
                    "session.logout",
                    "login_session",
                    principal.sessionId(),
                    "SUCCESS",
                    now);
        }
    }

    java.util.List<IdentityRepository.DeviceSession> sessions(SessionPrincipal principal) {
        return repository.findSessions(principal.userId());
    }

    SecurityEventPage securityEvents(
            SessionPrincipal principal,
            int requestedPageSize,
            String pageToken,
            String result,
            String deviceName,
            String requestId) {
        int pageSize = Math.max(1, Math.min(requestedPageSize, 100));
        SecurityEventCursor cursor = decodeSecurityEventCursor(pageToken);
        String normalizedResult = normalizeSecurityEventResult(result);
        String normalizedDevice = normalizeOptional(deviceName, 160);
        String normalizedRequestId = normalizeOptional(requestId, 160);
        List<IdentityRepository.SecurityEvent> rows = repository.findSecurityEvents(
                principal.userId(),
                normalizedResult,
                normalizedDevice,
                normalizedRequestId,
                cursor == null ? null : cursor.occurredAt(),
                cursor == null ? null : cursor.id(),
                pageSize + 1);
        boolean hasMore = rows.size() > pageSize;
        List<IdentityRepository.SecurityEvent> items = hasMore ? List.copyOf(rows.subList(0, pageSize)) : List.copyOf(rows);
        String nextPageToken = hasMore ? encodeSecurityEventCursor(items.get(items.size() - 1)) : null;
        return new SecurityEventPage(items, nextPageToken);
    }

    private static String normalizeSecurityEventResult(String result) {
        String normalized = normalizeOptional(result, 20);
        if (normalized == null) return null;
        normalized = normalized.toUpperCase(Locale.ROOT);
        if (!normalized.equals("SUCCESS") && !normalized.equals("FAILED")) {
            throw new InvalidSecurityEventQueryException();
        }
        return normalized;
    }

    private static String normalizeOptional(String value, int maxLength) {
        if (value == null || value.isBlank()) return null;
        String normalized = value.trim();
        if (normalized.length() > maxLength) throw new InvalidSecurityEventQueryException();
        return normalized;
    }

    private static SecurityEventCursor decodeSecurityEventCursor(String pageToken) {
        if (pageToken == null || pageToken.isBlank()) return null;
        try {
            String decoded = new String(Base64.getUrlDecoder().decode(pageToken), StandardCharsets.UTF_8);
            int separator = decoded.indexOf('|');
            if (separator <= 0) throw new IllegalArgumentException();
            return new SecurityEventCursor(
                    Instant.parse(decoded.substring(0, separator)), UUID.fromString(decoded.substring(separator + 1)));
        } catch (IllegalArgumentException error) {
            throw new InvalidSecurityEventQueryException();
        }
    }

    private static String encodeSecurityEventCursor(IdentityRepository.SecurityEvent event) {
        String raw = event.occurredAt() + "|" + event.id();
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }

    long accountVersion(SessionPrincipal principal) {
        return repository.findUserVersion(principal.userId());
    }

    @Transactional
    void revokeSession(
            SessionPrincipal principal,
            UUID sessionId,
            String idempotencyKey,
            String requestId) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new SessionContextService.IdempotencyKeyRequiredException();
        }
        String key = idempotencyKey.trim();
        if (key.length() > 160) {
            throw new SessionContextService.InvalidIdempotencyKeyException();
        }
        String fingerprint = TokenHasher.sha256(sessionId.toString());
        repository.lockAccountCommand(principal.userId(), "session.revoke", key);
        IdentityRepository.AccountCommandReceipt receipt =
                repository.findAccountCommandReceipt(principal.userId(), "session.revoke", key);
        if (receipt != null) {
            if (!receipt.requestFingerprint().equals(fingerprint)) {
                throw new SessionContextService.IdempotencyConflictException();
            }
            return;
        }
        Instant now = Instant.now();
        boolean revoked = repository.revokeSession(principal.userId(), sessionId, "REMOTE_LOGOUT", now);
        if (!revoked) {
            throw new SessionNotFoundException();
        }
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                repository.findCurrentWorkspaceId(principal.sessionId()),
                "session.revoke",
                "login_session",
                sessionId,
                "SUCCESS",
                now);
        repository.insertAccountCommandReceipt(
                principal.userId(), "session.revoke", key, fingerprint, now);
    }

    @Transactional
    void revokeOtherSessions(SessionPrincipal principal, String idempotencyKey, String requestId) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new SessionContextService.IdempotencyKeyRequiredException();
        }
        String key = idempotencyKey.trim();
        if (key.length() > 160) {
            throw new SessionContextService.InvalidIdempotencyKeyException();
        }
        String action = "session.revoke-others";
        String fingerprint = TokenHasher.sha256(principal.sessionId().toString());
        repository.lockAccountCommand(principal.userId(), action, key);
        IdentityRepository.AccountCommandReceipt receipt =
                repository.findAccountCommandReceipt(principal.userId(), action, key);
        if (receipt != null) {
            if (!receipt.requestFingerprint().equals(fingerprint)) {
                throw new SessionContextService.IdempotencyConflictException();
            }
            return;
        }
        Instant now = Instant.now();
        repository.revokeOtherSessions(
                principal.userId(), principal.sessionId(), "SECURITY_REVOKE_OTHERS", now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                repository.findCurrentWorkspaceId(principal.sessionId()),
                action,
                "user",
                principal.userId(),
                "SUCCESS",
                now);
        repository.insertAccountCommandReceipt(principal.userId(), action, key, fingerprint, now);
    }

    @Transactional
    void requestPasswordReset(String email, String requestId, String requestedIp) {
        String normalizedEmail = email.trim().toLowerCase(Locale.ROOT);
        IdentityRepository.UserIdentity user =
                repository.findUserByIdentifierHash(TokenHasher.sha256(normalizedEmail));
        if (user == null) {
            return;
        }
        Instant now = Instant.now();
        String token = TokenHasher.newToken();
        repository.insertPasswordResetChallenge(
                UuidV7.randomUuid(),
                user.id(),
                TokenHasher.sha256(token),
                now.plus(30, ChronoUnit.MINUTES),
                requestedIp,
                now);
        try {
            String messageId = passwordResetDeliveryService.send(requestId, user.email(), token);
            repository.insertDeliveryAudit(
                    UuidV7.randomUuid(), requestId, user.id(), "SENT", messageId, now);
        } catch (PasswordResetDeliveryService.DeliveryUnavailableException error) {
            repository.insertDeliveryAudit(
                    UuidV7.randomUuid(), requestId, user.id(), "FAILED", null, now);
        }
    }

    @Transactional
    void resetPassword(String token, String newPassword, String requestId) {
        IdentityRepository.PasswordResetChallenge challenge =
                repository.findPasswordResetChallengeForUpdate(TokenHasher.sha256(token));
        if (challenge == null) {
            throw new InvalidPasswordResetTokenException();
        }
        Instant now = Instant.now();
        repository.completePasswordReset(challenge, passwordEncoder.encode(newPassword), now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                challenge.userId(),
                null,
                "identity.password.reset",
                "user",
                challenge.userId(),
                "SUCCESS",
                now);
    }

    @Transactional
    void cancelAccount(
            SessionPrincipal principal,
            String password,
            String confirmation,
            long expectedVersion,
            String idempotencyKey,
            String requestId) {
        if (!"CANCEL".equals(confirmation)) {
            throw new AccountCancellationConfirmationException();
        }
        if (idempotencyKey == null || idempotencyKey.isBlank() || idempotencyKey.trim().length() > 160) {
            throw new SessionContextService.IdempotencyKeyRequiredException();
        }
        String key = idempotencyKey.trim();
        String fingerprint = TokenHasher.sha256(confirmation + "\u0000" + expectedVersion);
        repository.lockAccountCommand(principal.userId(), "account.cancel", key);
        IdentityRepository.AccountCommandReceipt receipt =
                repository.findAccountCommandReceipt(principal.userId(), "account.cancel", key);
        if (receipt != null) {
            if (!receipt.requestFingerprint().equals(fingerprint)) {
                throw new SessionContextService.IdempotencyConflictException();
            }
            return;
        }
        String passwordHash = repository.findPasswordHashForUserForUpdate(principal.userId());
        if (passwordHash == null || !passwordEncoder.matches(password, passwordHash)) {
            throw new InvalidCredentialsException();
        }
        IdentityRepository.UserAccountState account = repository.findUserAccountStateForUpdate(principal.userId());
        if (account == null || account.version() != expectedVersion) {
            throw new AccountVersionConflictException();
        }
        if (repository.countOwnedActiveTeamWorkspaces(principal.userId()) > 0) {
            throw new OwnedTeamWorkspaceExistsException();
        }
        Instant now = Instant.now();
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                repository.findCurrentWorkspaceId(principal.sessionId()),
                "identity.account.cancel",
                "user",
                principal.userId(),
                "SUCCESS",
                now);
        if (!repository.cancelAccount(principal.userId(), expectedVersion, now)) {
            throw new AccountVersionConflictException();
        }
        repository.insertAccountCommandReceipt(
                principal.userId(), "account.cancel", key, fingerprint, now);
    }

    IdentityRepository.AccountCancellationImpact accountCancellationImpact(SessionPrincipal principal) {
        return repository.accountCancellationImpact(principal.userId());
    }

    @Transactional
    AccountExport exportAccount(SessionPrincipal principal, String requestId) {
        Instant now = Instant.now();
        AccountExport result = new AccountExport(
                principal.userId(),
                principal.email(),
                principal.displayName(),
                repository.findUserAccountStateForUpdate(principal.userId()).version(),
                repository.findAccessibleWorkspaces(principal.userId()),
                repository.findSessions(principal.userId()),
                repository.findSecurityEvents(principal.userId(), 100),
                repository.findExternalIdentities(principal.userId()),
                now);
        repository.insertAuditLog(
                UuidV7.randomUuid(),
                requestId,
                principal.userId(),
                repository.findCurrentWorkspaceId(principal.sessionId()),
                "identity.account.export",
                "user",
                principal.userId(),
                "SUCCESS",
                now);
        return result;
    }

    record Registration(UUID userId, String email, String displayName, UUID workspaceId, String workspaceName) {}

    record SessionTokens(String accessToken, String refreshToken, Instant accessExpiresAt, Instant refreshExpiresAt) {}

    record LoginResult(UUID userId, SessionTokens tokens) {}

    record StepUpToken(String token, Instant expiresAt) {}

    record SecurityEventCursor(Instant occurredAt, UUID id) {}

    record SecurityEventPage(List<IdentityRepository.SecurityEvent> items, String nextPageToken) {}

    record AccountExport(
            UUID userId,
            String email,
            String displayName,
            long version,
            java.util.List<IdentityRepository.WorkspaceAccess> workspaces,
            java.util.List<IdentityRepository.DeviceSession> sessions,
            java.util.List<IdentityRepository.SecurityEvent> securityEvents,
            java.util.List<IdentityRepository.ExternalIdentity> externalIdentities,
            Instant exportedAt) {}

    static class DuplicateEmailException extends RuntimeException {}

    static class InvalidCredentialsException extends RuntimeException {
        private final UUID userId;

        InvalidCredentialsException() {
            this(null);
        }

        InvalidCredentialsException(UUID userId) {
            this.userId = userId;
        }

        UUID userId() {
            return userId;
        }
    }

    static class InvalidRefreshTokenException extends RuntimeException {}

    static class SessionNotFoundException extends RuntimeException {}

    static class InvalidPasswordResetTokenException extends RuntimeException {}

    static class InvalidSecurityEventQueryException extends RuntimeException {}

    static class AccountCancellationConfirmationException extends RuntimeException {}

    static class AccountVersionConflictException extends RuntimeException {}

    static class OwnedTeamWorkspaceExistsException extends RuntimeException {}

    static class OidcBindingRequiredException extends RuntimeException {}

    static class OidcEmailMismatchException extends RuntimeException {}

    static class OidcIdentityConflictException extends RuntimeException {}

    static class OidcIdentityNotFoundException extends RuntimeException {}

    static class StepUpRequiredException extends RuntimeException {}
}
