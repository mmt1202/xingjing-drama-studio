package com.xingjing.platform.identity;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

@Testcontainers
@SpringBootTest
class IdentityDatabaseMigrationTest {
    @Container
    @ServiceConnection
    static PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:17.6-alpine");

    @Autowired
    JdbcTemplate jdbc;

    @Test
    void createsSessionRolePermissionAndAppendOnlyAuditTables() {
        List<String> tableNames = jdbc.queryForList(
                "select table_name from information_schema.tables where table_schema = 'public'",
                String.class);

        assertThat(tableNames)
                .contains(
                        "user_credentials",
                        "login_sessions",
                        "roles",
                        "permissions",
                        "role_permissions",
                        "member_roles",
                        "audit_logs");

        List<String> sessionColumns = jdbc.queryForList(
                "select column_name from information_schema.columns "
                        + "where table_schema = 'public' and table_name = 'login_sessions'",
                String.class);
        assertThat(sessionColumns)
                .contains("token_hash", "refresh_token_hash", "expires_at", "refresh_expires_at", "revoked_at");

        List<String> uniqueIndexes = jdbc.queryForList(
                "select indexname from pg_indexes where schemaname = 'public' and tablename = 'login_sessions'",
                String.class);
        assertThat(uniqueIndexes).contains("uq_login_sessions_token_hash", "uq_login_sessions_refresh_token_hash");

        List<String> auditColumns = jdbc.queryForList(
                "select column_name from information_schema.columns "
                        + "where table_schema = 'public' and table_name = 'audit_logs'",
                String.class);
        assertThat(auditColumns)
                .contains("request_id", "actor_id", "workspace_id", "action", "target_type", "target_id", "result", "occurred_at");
    }
}
