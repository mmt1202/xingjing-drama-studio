package com.xingjing.platform.db;

import static org.assertj.core.api.Assertions.assertThat;

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
class DatabaseMigrationTest {
    @Container
    @ServiceConnection
    static PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:17.6-alpine");

    @Autowired
    JdbcTemplate jdbc;

    @Test
    void createsIdentityFoundationTables() {
        var names = jdbc.queryForList(
                "select table_name from information_schema.tables where table_schema = 'public'",
                String.class);
        assertThat(names).contains("users", "workspaces", "workspace_members", "flyway_schema_history");
    }
}
