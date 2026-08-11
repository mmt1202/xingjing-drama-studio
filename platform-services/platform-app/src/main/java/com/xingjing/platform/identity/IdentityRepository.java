package com.xingjing.platform.identity;

import java.time.Instant;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.UUID;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
class IdentityRepository {
    private final JdbcTemplate jdbc;

    IdentityRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    boolean emailExists(String email) {
        return Boolean.TRUE.equals(jdbc.queryForObject("select exists(select 1 from users where email = ?)", Boolean.class, email));
    }

    void insertUser(UUID id, String email, String displayName, Instant now) {
        jdbc.update(
                "insert into users (id, email, display_name, status, created_at, created_by, updated_at, updated_by, version) "
                        + "values (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, 0)",
                id, email, displayName, Timestamp.from(now), id, Timestamp.from(now), id);
    }

    void insertCredential(UUID id, UUID userId, String identifierHash, String passwordHash, Instant now) {
        jdbc.update(
                "insert into user_credentials (id, user_id, identifier_hash, password_hash, created_at, updated_at, version) "
                        + "values (?, ?, ?, ?, ?, ?, 0)",
                id, userId, identifierHash, passwordHash, Timestamp.from(now), Timestamp.from(now));
    }

    void insertWorkspace(UUID id, UUID ownerId, String name, String slug, Instant now) {
        jdbc.update(
                "insert into workspaces (id, name, slug, status, plan_code, created_at, created_by, updated_at, updated_by, version) "
                        + "values (?, ?, ?, 'ACTIVE', 'PERSONAL_FREE', ?, ?, ?, ?, 0)",
                id, name, slug, Timestamp.from(now), ownerId, Timestamp.from(now), ownerId);
    }

    void insertTeamWorkspace(UUID id, UUID ownerId, String name, String slug, Instant now) {
        jdbc.update(
                "insert into workspaces (id, name, slug, status, kind, plan_code, created_at, created_by, updated_at, updated_by, version) "
                        + "values (?, ?, ?, 'ACTIVE', 'TEAM', 'TEAM_FREE', ?, ?, ?, ?, 0)",
                id,
                name,
                slug,
                Timestamp.from(now),
                ownerId,
                Timestamp.from(now),
                ownerId);
    }

    void insertOwnerMembership(UUID workspaceId, UUID userId, Instant now) {
        jdbc.update(
                "insert into workspace_members (workspace_id, user_id, role_key, status, joined_at, created_at, created_by, updated_at, updated_by, version) "
                        + "values (?, ?, 'OWNER', 'ACTIVE', ?, ?, ?, ?, ?, 0)",
                workspaceId, userId, Timestamp.from(now), Timestamp.from(now), userId, Timestamp.from(now), userId);
    }

    Credential findCredential(String identifierHash) {
        return jdbc.query(
                        "select u.id, c.password_hash from user_credentials c "
                                + "join users u on u.id = c.user_id "
                                + "where c.identifier_hash = ? and u.status = 'ACTIVE'",
                        (resultSet, rowNumber) -> new Credential(
                                resultSet.getObject("id", UUID.class), resultSet.getString("password_hash")),
                        identifierHash)
                .stream()
                .findFirst()
                .orElse(null);
    }

    CredentialState findCredentialStateForUpdate(String identifierHash) {
        return jdbc.query(
                        "select u.id, u.email, c.id as credential_id, c.password_hash, "
                                + "c.failed_count, c.locked_until from user_credentials c "
                                + "join users u on u.id = c.user_id "
                                + "where c.identifier_hash = ? and u.status = 'ACTIVE' for update",
                        (resultSet, rowNumber) -> new CredentialState(
                                resultSet.getObject("id", UUID.class),
                                resultSet.getString("email"),
                                resultSet.getObject("credential_id", UUID.class),
                                resultSet.getString("password_hash"),
                                resultSet.getInt("failed_count"),
                                resultSet.getTimestamp("locked_until") == null
                                        ? null
                                        : resultSet.getTimestamp("locked_until").toInstant()),
                        identifierHash)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void recordFailedLogin(UUID credentialId, int failedCount, Instant lockedUntil, Instant now) {
        jdbc.update(
                "update user_credentials set failed_count = ?, locked_until = ?, updated_at = ?, version = version + 1 "
                        + "where id = ?",
                failedCount,
                lockedUntil == null ? null : Timestamp.from(lockedUntil),
                Timestamp.from(now),
                credentialId);
    }

    void clearFailedLogin(UUID credentialId, Instant now) {
        jdbc.update(
                "update user_credentials set failed_count = 0, locked_until = null, updated_at = ?, version = version + 1 "
                        + "where id = ? and (failed_count <> 0 or locked_until is not null)",
                Timestamp.from(now),
                credentialId);
    }

    UserIdentity findUserByIdentifierHash(String identifierHash) {
        return jdbc.query(
                        "select u.id, u.email from user_credentials c join users u on u.id = c.user_id "
                                + "where c.identifier_hash = ? and u.status = 'ACTIVE'",
                        (resultSet, rowNumber) -> new UserIdentity(
                                resultSet.getObject("id", UUID.class), resultSet.getString("email")),
                        identifierHash)
                .stream()
                .findFirst()
                .orElse(null);
    }

    UserIdentity findUserByEmail(String email) {
        return jdbc.query(
                        "select id,email from users where email = ? and status = 'ACTIVE'",
                        (resultSet, rowNumber) -> new UserIdentity(
                                resultSet.getObject("id", UUID.class), resultSet.getString("email")),
                        email)
                .stream()
                .findFirst()
                .orElse(null);
    }

    UserIdentity findUserByExternalIdentity(String providerKey, String subject) {
        return jdbc.query(
                        "select u.id,u.email from external_identity_bindings b "
                                + "join users u on u.id=b.user_id "
                                + "where b.provider_key=? and b.subject=? and b.revoked_at is null and u.status='ACTIVE'",
                        (resultSet, rowNumber) -> new UserIdentity(
                                resultSet.getObject("id", UUID.class), resultSet.getString("email")),
                        providerKey,
                        subject)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void insertExternalIdentity(
            UUID id,
            UUID userId,
            String providerKey,
            String subject,
            String email,
            UUID actorId,
            Instant now) {
        jdbc.update(
                "insert into external_identity_bindings "
                        + "(id,user_id,provider_key,subject,email_at_binding,created_at,created_by) "
                        + "values (?,?,?,?,?,?,?)",
                id,
                userId,
                providerKey,
                subject,
                email,
                Timestamp.from(now),
                actorId);
    }

    List<ExternalIdentity> findExternalIdentities(UUID userId) {
        return jdbc.query(
                "select id,provider_key,subject,email_at_binding,created_at,revoked_at "
                        + "from external_identity_bindings where user_id=? order by created_at desc,id desc",
                (resultSet, rowNumber) -> new ExternalIdentity(
                        resultSet.getObject("id", UUID.class),
                        resultSet.getString("provider_key"),
                        resultSet.getString("subject"),
                        resultSet.getString("email_at_binding"),
                        resultSet.getTimestamp("created_at").toInstant(),
                        resultSet.getTimestamp("revoked_at") == null
                                ? null
                                : resultSet.getTimestamp("revoked_at").toInstant()),
                userId);
    }

    boolean revokeExternalIdentity(UUID userId, UUID bindingId, Instant now) {
        return jdbc.update(
                        "update external_identity_bindings set revoked_at=?,revoked_by=? "
                                + "where id=? and user_id=? and revoked_at is null",
                        Timestamp.from(now),
                        userId,
                        bindingId,
                        userId)
                == 1;
    }

    void insertStepUpChallenge(
            UUID id,
            UUID userId,
            UUID sessionId,
            String tokenHash,
            String purpose,
            Instant expiresAt,
            Instant now) {
        jdbc.update(
                "insert into step_up_challenges "
                        + "(id,user_id,session_id,token_hash,purpose,expires_at,created_at) values (?,?,?,?,?,?,?)",
                id,
                userId,
                sessionId,
                tokenHash,
                purpose,
                Timestamp.from(expiresAt),
                Timestamp.from(now));
    }

    boolean consumeStepUpChallenge(
            UUID userId, UUID sessionId, String tokenHash, String purpose, Instant now) {
        return jdbc.update(
                        "update step_up_challenges set consumed_at=? "
                                + "where user_id=? and session_id=? and token_hash=? and purpose=? "
                                + "and consumed_at is null and expires_at>?",
                        Timestamp.from(now),
                        userId,
                        sessionId,
                        tokenHash,
                        purpose,
                        Timestamp.from(now))
                == 1;
    }

    String findPasswordHashForUserForUpdate(UUID userId) {
        return jdbc.query(
                        "select password_hash from user_credentials where user_id = ? for update",
                        (resultSet, rowNumber) -> resultSet.getString("password_hash"),
                        userId)
                .stream()
                .findFirst()
                .orElse(null);
    }

    UserAccountState findUserAccountStateForUpdate(UUID userId) {
        return jdbc.query(
                        "select status,version from users where id=? for update",
                        (resultSet, rowNumber) ->
                                new UserAccountState(resultSet.getString("status"), resultSet.getLong("version")),
                        userId)
                .stream()
                .findFirst()
                .orElse(null);
    }

    long findUserVersion(UUID userId) {
        Long version = jdbc.queryForObject("select version from users where id=?", Long.class, userId);
        return version == null ? 0 : version;
    }

    int countOwnedActiveTeamWorkspaces(UUID userId) {
        Integer count = jdbc.queryForObject(
                "select count(*) from workspace_members wm join workspaces w on w.id=wm.workspace_id "
                        + "where wm.user_id=? and wm.role_key='OWNER' and wm.status='ACTIVE' "
                        + "and w.kind='TEAM' and w.status='ACTIVE'",
                Integer.class,
                userId);
        return count == null ? 0 : count;
    }

    AccountCancellationImpact accountCancellationImpact(UUID userId) {
        return jdbc.queryForObject(
                "select "
                        + "(select count(*) from workspace_members where user_id=? and status='ACTIVE') as active_memberships,"
                        + "(select count(*) from workspace_members wm join workspaces w on w.id=wm.workspace_id "
                        + " where wm.user_id=? and wm.role_key='OWNER' and wm.status='ACTIVE' "
                        + " and w.kind='TEAM' and w.status='ACTIVE') as owned_team_workspaces,"
                        + "(select count(*) from login_sessions where user_id=? and revoked_at is null) as active_sessions,"
                        + "(select count(*) from external_identities where user_id=? and revoked_at is null) as active_external_identities",
                (resultSet, rowNumber) -> new AccountCancellationImpact(
                        resultSet.getInt("active_memberships"),
                        resultSet.getInt("owned_team_workspaces"),
                        resultSet.getInt("active_sessions"),
                        resultSet.getInt("active_external_identities")),
                userId,
                userId,
                userId,
                userId);
    }

    void lockAccountCommand(UUID userId, String action, String idempotencyKey) {
        jdbc.queryForObject(
                "select pg_advisory_xact_lock(hashtextextended(?, 0))",
                Long.class,
                userId + "\u0000" + action + "\u0000" + idempotencyKey);
    }

    AccountCommandReceipt findAccountCommandReceipt(UUID userId, String action, String idempotencyKey) {
        return jdbc.query(
                        "select request_fingerprint from account_command_receipts "
                                + "where user_id=? and action=? and idempotency_key=?",
                        (resultSet, rowNumber) -> new AccountCommandReceipt(
                                resultSet.getString("request_fingerprint")),
                        userId,
                        action,
                        idempotencyKey)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void insertAccountCommandReceipt(
            UUID userId, String action, String idempotencyKey, String fingerprint, Instant now) {
        jdbc.update(
                "insert into account_command_receipts "
                        + "(user_id,action,idempotency_key,request_fingerprint,created_at) values (?,?,?,?,?)",
                userId,
                action,
                idempotencyKey,
                fingerprint,
                Timestamp.from(now));
    }

    boolean cancelAccount(UUID userId, long expectedVersion, Instant now) {
        int changed = jdbc.update(
                "update users set status = 'CANCELLED', updated_at = ?, updated_by = ?, version = version + 1 "
                        + "where id = ? and status = 'ACTIVE' and version=?",
                Timestamp.from(now),
                userId,
                userId,
                expectedVersion);
        if (changed != 1) {
            return false;
        }
        jdbc.update(
                "update workspace_members set status = 'REMOVED', updated_at = ?, updated_by = ?, version = version + 1 "
                        + "where user_id = ? and status = 'ACTIVE'",
                Timestamp.from(now),
                userId,
                userId);
        jdbc.update(
                "update login_sessions set revoked_at = ?, revoke_reason = 'ACCOUNT_CANCELLED' "
                        + "where user_id = ? and revoked_at is null",
                Timestamp.from(now),
                userId);
        return true;
    }

    List<SecurityEvent> findSecurityEvents(
            UUID userId,
            String result,
            String deviceName,
            String requestId,
            Instant cursorOccurredAt,
            UUID cursorId,
            int limit) {
        StringBuilder sql = new StringBuilder(
                "select id,request_id,result,reason,ip_address,region,device_name,occurred_at "
                        + "from login_security_events where user_id = ?");
        List<Object> parameters = new ArrayList<>();
        parameters.add(userId);
        if (result != null) {
            sql.append(" and result = ?");
            parameters.add(result);
        }
        if (deviceName != null) {
            sql.append(" and device_name ilike ? escape E'\\\\'");
            parameters.add("%" + escapeLike(deviceName) + "%");
        }
        if (requestId != null) {
            sql.append(" and request_id = ?");
            parameters.add(requestId);
        }
        if (cursorOccurredAt != null && cursorId != null) {
            sql.append(" and (occurred_at < ? or (occurred_at = ? and id < ?))");
            Timestamp cursor = Timestamp.from(cursorOccurredAt);
            parameters.add(cursor);
            parameters.add(cursor);
            parameters.add(cursorId);
        }
        sql.append(" order by occurred_at desc,id desc limit ?");
        parameters.add(limit);
        return jdbc.query(
                sql.toString(),
                (resultSet, rowNumber) -> new SecurityEvent(
                        resultSet.getObject("id", UUID.class),
                        resultSet.getString("request_id"),
                        resultSet.getString("result"),
                        resultSet.getString("reason"),
                        resultSet.getString("ip_address"),
                        resultSet.getString("region"),
                        resultSet.getString("device_name"),
                        resultSet.getTimestamp("occurred_at").toInstant()),
                parameters.toArray());
    }

    List<SecurityEvent> findSecurityEvents(UUID userId, int limit) {
        return findSecurityEvents(userId, null, null, null, null, null, limit);
    }

    private static String escapeLike(String value) {
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    void insertPasswordResetChallenge(
            UUID id, UUID userId, String tokenHash, Instant expiresAt, String requestedIp, Instant now) {
        jdbc.update(
                "insert into password_reset_challenges "
                        + "(id,user_id,token_hash,expires_at,requested_ip,created_at) values (?,?,?,?,?,?)",
                id,
                userId,
                tokenHash,
                Timestamp.from(expiresAt),
                requestedIp,
                Timestamp.from(now));
    }

    PasswordResetChallenge findPasswordResetChallengeForUpdate(String tokenHash) {
        return jdbc.query(
                        "select id,user_id from password_reset_challenges "
                                + "where token_hash = ? and consumed_at is null and expires_at > current_timestamp for update",
                        (resultSet, rowNumber) -> new PasswordResetChallenge(
                                resultSet.getObject("id", UUID.class),
                                resultSet.getObject("user_id", UUID.class)),
                        tokenHash)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void completePasswordReset(
            PasswordResetChallenge challenge, String passwordHash, Instant now) {
        jdbc.update(
                "update user_credentials set password_hash = ?, failed_count = 0, locked_until = null, "
                        + "updated_at = ?, version = version + 1 where user_id = ?",
                passwordHash,
                Timestamp.from(now),
                challenge.userId());
        jdbc.update(
                "update password_reset_challenges set consumed_at = ? where id = ? and consumed_at is null",
                Timestamp.from(now),
                challenge.id());
        jdbc.update(
                "update login_sessions set revoked_at = ?, revoke_reason = 'PASSWORD_RESET' "
                        + "where user_id = ? and revoked_at is null",
                Timestamp.from(now),
                challenge.userId());
    }

    void insertDeliveryAudit(
            UUID id,
            String requestId,
            UUID userId,
            String outcome,
            String providerMessageId,
            Instant now) {
        jdbc.update(
                "insert into identity_delivery_audit "
                        + "(id,request_id,user_id,channel,template_key,outcome,provider_message_id,occurred_at) "
                        + "values (?, ?, ?, 'EMAIL', 'identity.password-reset', ?, ?, ?)",
                id,
                requestId,
                userId,
                outcome,
                providerMessageId,
                Timestamp.from(now));
    }

    void insertSession(
            UUID sessionId,
            UUID userId,
            String accessTokenHash,
            String refreshTokenHash,
            String deviceName,
            String riskStatus,
            Instant accessExpiresAt,
            Instant refreshExpiresAt,
            Instant now) {
        jdbc.update(
                "insert into login_sessions (id, user_id, token_hash, refresh_token_hash, device_name, risk_status, expires_at, "
                        + "refresh_expires_at, last_seen_at, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                sessionId,
                userId,
                accessTokenHash,
                refreshTokenHash,
                deviceName,
                riskStatus,
                Timestamp.from(accessExpiresAt),
                Timestamp.from(refreshExpiresAt),
                Timestamp.from(now),
                Timestamp.from(now));
    }

    void insertRotatedSession(
            UUID sessionId,
            UUID userId,
            String accessTokenHash,
            String refreshTokenHash,
            String deviceName,
            String riskStatus,
            Instant accessExpiresAt,
            Instant refreshExpiresAt,
            UUID currentWorkspaceId,
            Instant now) {
        jdbc.update(
                "insert into login_sessions (id, user_id, token_hash, refresh_token_hash, device_name, risk_status, expires_at, "
                        + "refresh_expires_at, current_workspace_id, last_seen_at, created_at) "
                        + "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                sessionId,
                userId,
                accessTokenHash,
                refreshTokenHash,
                deviceName,
                riskStatus,
                Timestamp.from(accessExpiresAt),
                Timestamp.from(refreshExpiresAt),
                currentWorkspaceId,
                Timestamp.from(now),
                Timestamp.from(now));
    }

    SessionPrincipal findActiveSessionByAccessTokenHash(String accessTokenHash) {
        return jdbc.query(
                        "select s.id as session_id, u.id as user_id, u.email, u.display_name "
                                + "from login_sessions s join users u on u.id = s.user_id "
                                + "where s.token_hash = ? and s.revoked_at is null and s.expires_at > current_timestamp "
                                + "and u.status = 'ACTIVE'",
                        (resultSet, rowNumber) -> new SessionPrincipal(
                                resultSet.getObject("user_id", UUID.class),
                                resultSet.getObject("session_id", UUID.class),
                                resultSet.getString("email"),
                                resultSet.getString("display_name")),
                        accessTokenHash)
                .stream()
                .findFirst()
                .orElse(null);
    }

    RefreshSession findActiveSessionByRefreshTokenHashForUpdate(String refreshTokenHash) {
        return jdbc.query(
                        "select id, user_id, device_name, risk_status, current_workspace_id from login_sessions "
                                + "where refresh_token_hash = ? and revoked_at is null "
                                + "and refresh_expires_at > current_timestamp for update",
                        (resultSet, rowNumber) -> new RefreshSession(
                                resultSet.getObject("id", UUID.class),
                                resultSet.getObject("user_id", UUID.class),
                                resultSet.getString("device_name"),
                                resultSet.getString("risk_status"),
                                resultSet.getObject("current_workspace_id", UUID.class)),
                        refreshTokenHash)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void revokeReplacedSession(UUID sessionId, UUID replacementSessionId, Instant now) {
        jdbc.update(
                "update login_sessions set revoked_at = ?, revoke_reason = 'REFRESHED', replaced_by_session_id = ? "
                        + "where id = ? and revoked_at is null",
                Timestamp.from(now),
                replacementSessionId,
                sessionId);
    }

    boolean revokeSession(UUID userId, UUID sessionId, String reason, Instant now) {
        return jdbc.update(
                        "update login_sessions set revoked_at = ?, revoke_reason = ? "
                                + "where id = ? and user_id = ? and revoked_at is null",
                        Timestamp.from(now),
                        reason,
                        sessionId,
                        userId)
                == 1;
    }

    int revokeOtherSessions(UUID userId, UUID currentSessionId, String reason, Instant now) {
        return jdbc.update(
                "update login_sessions set revoked_at = ?, revoke_reason = ? "
                        + "where user_id = ? and id <> ? and revoked_at is null",
                Timestamp.from(now),
                reason,
                userId,
                currentSessionId);
    }

    List<DeviceSession> findSessions(UUID userId) {
        return jdbc.query(
                "select id, device_name, risk_status, expires_at, refresh_expires_at, revoked_at, last_seen_at, created_at "
                        + "from login_sessions where user_id = ? order by created_at desc, id desc",
                (resultSet, rowNumber) -> new DeviceSession(
                        resultSet.getObject("id", UUID.class),
                        resultSet.getString("device_name"),
                        resultSet.getString("risk_status"),
                        resultSet.getTimestamp("expires_at").toInstant(),
                        resultSet.getTimestamp("refresh_expires_at").toInstant(),
                        resultSet.getTimestamp("revoked_at") == null
                                ? null
                                : resultSet.getTimestamp("revoked_at").toInstant(),
                        resultSet.getTimestamp("last_seen_at").toInstant(),
                        resultSet.getTimestamp("created_at").toInstant()),
                userId);
    }

    List<WorkspaceAccess> findAccessibleWorkspaces(UUID userId) {
        return jdbc.query(
                "select w.id, w.name, w.slug, w.status, w.kind, w.plan_code, wm.role_key, "
                        + "wm.last_selected_at, w.version "
                        + "from workspace_members wm join workspaces w on w.id = wm.workspace_id "
                        + "where wm.user_id = ? and wm.status = 'ACTIVE' and w.status = 'ACTIVE' "
                        + "order by wm.last_selected_at desc nulls last, w.created_at asc, w.id asc",
                (resultSet, rowNumber) -> new WorkspaceAccess(
                        resultSet.getObject("id", UUID.class),
                        resultSet.getString("name"),
                        resultSet.getString("slug"),
                        resultSet.getString("status"),
                        resultSet.getString("kind"),
                        resultSet.getString("plan_code"),
                        resultSet.getString("role_key"),
                        resultSet.getTimestamp("last_selected_at") == null
                                ? null
                                : resultSet.getTimestamp("last_selected_at").toInstant(),
                        resultSet.getLong("version")),
                userId);
    }

    WorkspaceAccess findWorkspaceAccess(UUID userId, UUID workspaceId, boolean includeClosed) {
        String statusFilter = includeClosed ? "" : " and w.status = 'ACTIVE'";
        return jdbc.query(
                        "select w.id,w.name,w.slug,w.status,w.kind,w.plan_code,wm.role_key,"
                                + "wm.last_selected_at,w.version "
                                + "from workspace_members wm join workspaces w on w.id=wm.workspace_id "
                                + "where wm.user_id=? and wm.workspace_id=?"
                                + statusFilter,
                        (resultSet, rowNumber) -> new WorkspaceAccess(
                                resultSet.getObject("id", UUID.class),
                                resultSet.getString("name"),
                                resultSet.getString("slug"),
                                resultSet.getString("status"),
                                resultSet.getString("kind"),
                                resultSet.getString("plan_code"),
                                resultSet.getString("role_key"),
                                resultSet.getTimestamp("last_selected_at") == null
                                        ? null
                                        : resultSet.getTimestamp("last_selected_at").toInstant(),
                                resultSet.getLong("version")),
                        userId,
                        workspaceId)
                .stream()
                .findFirst()
                .orElse(null);
    }

    OwnedWorkspace findOwnedWorkspaceForUpdate(UUID userId, UUID workspaceId) {
        return jdbc.query(
                        "select w.id,w.kind,w.status,w.version from workspaces w "
                                + "join workspace_members wm on wm.workspace_id=w.id "
                                + "where w.id=? and wm.user_id=? and wm.role_key='OWNER' "
                                + "and wm.status='ACTIVE' for update",
                        (resultSet, rowNumber) -> new OwnedWorkspace(
                                resultSet.getObject("id", UUID.class),
                                resultSet.getString("kind"),
                                resultSet.getString("status"),
                                resultSet.getLong("version")),
                        workspaceId,
                        userId)
                .stream()
                .findFirst()
                .orElse(null);
    }

    boolean activeWorkspaceMemberForUpdate(UUID workspaceId, UUID userId) {
        return !jdbc.query(
                        "select user_id from workspace_members "
                                + "where workspace_id=? and user_id=? and status='ACTIVE' for update",
                        (resultSet, rowNumber) -> resultSet.getObject("user_id", UUID.class),
                        workspaceId,
                        userId)
                .isEmpty();
    }

    boolean transferWorkspaceOwnership(
            UUID workspaceId,
            UUID currentOwnerId,
            UUID targetOwnerId,
            long expectedVersion,
            Instant now) {
        int workspaceChanged = jdbc.update(
                "update workspaces set updated_at=?,updated_by=?,version=version+1 "
                        + "where id=? and kind='TEAM' and status='ACTIVE' and version=?",
                Timestamp.from(now),
                currentOwnerId,
                workspaceId,
                expectedVersion);
        if (workspaceChanged != 1) return false;
        int targetChanged = jdbc.update(
                "update workspace_members set role_key='OWNER',updated_at=?,updated_by=?,version=version+1 "
                        + "where workspace_id=? and user_id=? and status='ACTIVE'",
                Timestamp.from(now),
                currentOwnerId,
                workspaceId,
                targetOwnerId);
        int ownerChanged = jdbc.update(
                "update workspace_members set role_key='ADMIN',updated_at=?,updated_by=?,version=version+1 "
                        + "where workspace_id=? and user_id=? and role_key='OWNER' and status='ACTIVE'",
                Timestamp.from(now),
                currentOwnerId,
                workspaceId,
                currentOwnerId);
        return targetChanged == 1 && ownerChanged == 1;
    }

    WorkspaceCommandReceipt findWorkspaceCommandReceipt(UUID actorId, String action, String idempotencyKey) {
        return jdbc.query(
                        "select request_fingerprint,workspace_id from workspace_command_receipts "
                                + "where actor_id=? and action=? and idempotency_key=?",
                        (resultSet, rowNumber) -> new WorkspaceCommandReceipt(
                                resultSet.getString("request_fingerprint"),
                                resultSet.getObject("workspace_id", UUID.class)),
                        actorId,
                        action,
                        idempotencyKey)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void lockWorkspaceCommand(UUID actorId, String action, String idempotencyKey) {
        jdbc.queryForObject(
                "select pg_advisory_xact_lock(hashtextextended(?, 0))",
                Long.class,
                actorId + "\u0000" + action + "\u0000" + idempotencyKey);
    }

    void insertWorkspaceCommandReceipt(
            UUID actorId,
            String action,
            String idempotencyKey,
            String fingerprint,
            UUID workspaceId,
            Instant now) {
        jdbc.update(
                "insert into workspace_command_receipts "
                        + "(actor_id,action,idempotency_key,request_fingerprint,workspace_id,created_at) "
                        + "values (?,?,?,?,?,?)",
                actorId,
                action,
                idempotencyKey,
                fingerprint,
                workspaceId,
                Timestamp.from(now));
    }

    void archiveWorkspace(UUID workspaceId, long expectedVersion, UUID actorId, Instant now) {
        jdbc.update(
                "update workspaces set status='CLOSED',updated_at=?,updated_by=?,version=version+1 "
                        + "where id=? and version=? and status='ACTIVE'",
                Timestamp.from(now),
                actorId,
                workspaceId,
                expectedVersion);
    }

    void revokeWorkspaceSessions(UUID workspaceId, Instant now) {
        jdbc.update(
                "update login_sessions set current_workspace_id=null,last_seen_at=? "
                        + "where current_workspace_id=? and revoked_at is null",
                Timestamp.from(now),
                workspaceId);
    }

    UUID findCurrentWorkspaceId(UUID sessionId) {
        return jdbc.query(
                "select current_workspace_id from login_sessions where id = ?",
                resultSet -> resultSet.next() ? resultSet.getObject("current_workspace_id", UUID.class) : null,
                sessionId);
    }

    boolean hasActiveWorkspaceMembership(UUID userId, UUID workspaceId) {
        return Boolean.TRUE.equals(jdbc.queryForObject(
                "select exists(select 1 from workspace_members wm join workspaces w on w.id = wm.workspace_id "
                        + "where wm.user_id = ? and wm.workspace_id = ? and wm.status = 'ACTIVE' and w.status = 'ACTIVE')",
                Boolean.class,
                userId,
                workspaceId));
    }

    WorkspaceSelection findWorkspaceSelection(UUID sessionId, String idempotencyKey) {
        return jdbc.query(
                        "select workspace_id, request_id from session_workspace_selections "
                                + "where session_id = ? and idempotency_key = ?",
                        (resultSet, rowNumber) -> new WorkspaceSelection(
                                resultSet.getObject("workspace_id", UUID.class), resultSet.getString("request_id")),
                        sessionId,
                        idempotencyKey)
                .stream()
                .findFirst()
                .orElse(null);
    }

    void selectWorkspace(UUID sessionId, String idempotencyKey, UUID workspaceId, String requestId, Instant now) {
        jdbc.update(
                "insert into session_workspace_selections (session_id, idempotency_key, workspace_id, request_id, selected_at) "
                        + "values (?, ?, ?, ?, ?)",
                sessionId,
                idempotencyKey,
                workspaceId,
                requestId,
                Timestamp.from(now));
        jdbc.update("update login_sessions set current_workspace_id = ?, last_seen_at = ? where id = ?",
                workspaceId, Timestamp.from(now), sessionId);
        jdbc.update(
                "update workspace_members wm set last_selected_at=? "
                        + "from login_sessions s where wm.workspace_id=? and wm.user_id=s.user_id and s.id=?",
                Timestamp.from(now),
                workspaceId,
                sessionId);
    }

    void insertAuditLog(
            UUID id,
            String requestId,
            UUID actorId,
            UUID workspaceId,
            String action,
            String targetType,
            UUID targetId,
            String result,
            Instant occurredAt) {
        jdbc.update(
                "insert into audit_logs (id, request_id, actor_id, workspace_id, action, target_type, target_id, result, occurred_at) "
                        + "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                id,
                requestId,
                actorId,
                workspaceId,
                action,
                targetType,
                targetId,
                result,
                Timestamp.from(occurredAt));
    }

    record Credential(UUID userId, String passwordHash) {}

    record CredentialState(
            UUID userId,
            String email,
            UUID credentialId,
            String passwordHash,
            int failedCount,
            Instant lockedUntil) {}

    record UserIdentity(UUID id, String email) {}

    record PasswordResetChallenge(UUID id, UUID userId) {}

    record SecurityEvent(
            UUID id,
            String requestId,
            String result,
            String reason,
            String ipAddress,
            String region,
            String deviceName,
            Instant occurredAt) {}

    record ExternalIdentity(
            UUID id,
            String providerKey,
            String subject,
            String emailAtBinding,
            Instant createdAt,
            Instant revokedAt) {}

    record RefreshSession(UUID id, UUID userId, String deviceName, String riskStatus, UUID currentWorkspaceId) {}

    record DeviceSession(
            UUID id,
            String deviceName,
            String riskStatus,
            Instant accessExpiresAt,
            Instant refreshExpiresAt,
            Instant revokedAt,
            Instant lastSeenAt,
            Instant createdAt) {}

    record WorkspaceAccess(
            UUID id,
            String name,
            String slug,
            String status,
            String kind,
            String planCode,
            String role,
            Instant lastSelectedAt,
            long version) {}

    record WorkspaceSelection(UUID workspaceId, String requestId) {}

    record OwnedWorkspace(UUID id, String kind, String status, long version) {}

    record WorkspaceCommandReceipt(String requestFingerprint, UUID workspaceId) {}

    record UserAccountState(String status, long version) {}

    record AccountCommandReceipt(String requestFingerprint) {}

    record AccountCancellationImpact(
            int activeMemberships,
            int ownedTeamWorkspaces,
            int activeSessions,
            int activeExternalIdentities) {}
}
