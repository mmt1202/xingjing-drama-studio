package com.xingjing.platform.identity;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import tools.jackson.databind.ObjectMapper;

@Testcontainers
@SpringBootTest
@AutoConfigureMockMvc
class AuthApiIntegrationTest {
    @Container
    @ServiceConnection
    static PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:17.6-alpine");

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    ObjectMapper objectMapper;

    @BeforeEach
    void clearIdentityData() {
        jdbc.execute("truncate table audit_logs, member_roles, role_permissions, roles, permissions, "
                + "login_sessions, user_credentials, workspace_members, workspaces, users cascade");
    }

    @Test
    void registrationCreatesCredentialPersonalWorkspaceAndOwnerMembership() throws Exception {
        mvc.perform(post("/api/v1/auth/register")
                        .header("X-Request-Id", "req-register-alice")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "email":"alice@example.com",
                                  "password":"StrongPassword-123!",
                                  "displayName":"Alice"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.user.id").isNotEmpty())
                .andExpect(jsonPath("$.data.workspace.id").isNotEmpty())
                .andExpect(jsonPath("$.data.workspace.role").value("OWNER"))
                .andExpect(jsonPath("$.meta.requestId").value("req-register-alice"));

        assertThat(jdbc.queryForObject("select count(*) from users where email = 'alice@example.com'", Integer.class))
                .isEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from user_credentials", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from workspaces", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from workspace_members where role_key = 'OWNER'", Integer.class))
                .isEqualTo(1);
    }

    @Test
    void loginCreatesOpaqueAccessAndRefreshTokens() throws Exception {
        mvc.perform(post("/api/v1/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "email":"login@example.com",
                                  "password":"StrongPassword-123!",
                                  "displayName":"Login User"
                                }
                                """))
                .andExpect(status().isCreated());

        mvc.perform(post("/api/v1/auth/login")
                        .header("X-Request-Id", "req-login-user")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "email":"login@example.com",
                                  "password":"StrongPassword-123!",
                                  "deviceName":"Chrome on Windows"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.accessToken").isNotEmpty())
                .andExpect(jsonPath("$.data.refreshToken").isNotEmpty())
                .andExpect(jsonPath("$.data.accessToken").value(org.hamcrest.Matchers.not("StrongPassword-123!")))
                .andExpect(jsonPath("$.meta.requestId").value("req-login-user"));

        assertThat(jdbc.queryForObject("select count(*) from login_sessions", Integer.class)).isEqualTo(1);
    }

    @Test
    void bearerTokenControlsProfileAccessAndInvalidTokenUsesErrorEnvelope() throws Exception {
        mvc.perform(post("/api/v1/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "email":"profile@example.com",
                                  "password":"StrongPassword-123!",
                                  "displayName":"Profile User"
                                }
                                """))
                .andExpect(status().isCreated());

        MvcResult login = mvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "email":"profile@example.com",
                                  "password":"StrongPassword-123!"
                                }
                                """))
                .andExpect(status().isOk())
                .andReturn();
        String accessToken = objectMapper.readTree(login.getResponse().getContentAsString())
                .path("data")
                .path("accessToken")
                .asString();

        mvc.perform(get("/api/v1/account/profile").header("Authorization", "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.email").value("profile@example.com"));

        mvc.perform(get("/api/v1/account/profile")
                        .header("Authorization", "Bearer invalid-token")
                        .header("X-Request-Id", "req-invalid-token"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("UNAUTHENTICATED"))
                .andExpect(jsonPath("$.meta.requestId").value("req-invalid-token"));
    }

    @Test
    void sessionContextListsAccessibleWorkspacesAndStartsWithoutSelection() throws Exception {
        String accessToken = registerAndLogin("context@example.com", "Context User");

        mvc.perform(get("/api/v1/session/context")
                        .header("Authorization", "Bearer " + accessToken)
                        .header("X-Request-Id", "req-session-context"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.user.email").value("context@example.com"))
                .andExpect(jsonPath("$.data.currentWorkspace").doesNotExist())
                .andExpect(jsonPath("$.data.workspaces.length()").value(1))
                .andExpect(jsonPath("$.data.workspaces[0].role").value("OWNER"))
                .andExpect(jsonPath("$.meta.requestId").value("req-session-context"));
    }

    @Test
    void selectingAccessibleWorkspacePersistsSessionContextAndWritesAuditLog() throws Exception {
        String accessToken = registerAndLogin("select@example.com", "Select User");
        String workspaceId = jdbc.queryForObject(
                "select id::text from workspaces where created_by = (select id from users where email = 'select@example.com')",
                String.class);

        mvc.perform(put("/api/v1/session/context/workspace")
                        .header("Authorization", "Bearer " + accessToken)
                        .header("Idempotency-Key", "select-personal-workspace")
                        .header("X-Request-Id", "req-select-workspace")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"workspaceId\":\"" + workspaceId + "\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.currentWorkspace.id").value(workspaceId))
                .andExpect(jsonPath("$.data.currentWorkspace.role").value("OWNER"));

        mvc.perform(get("/api/v1/session/context").header("Authorization", "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.currentWorkspace.id").value(workspaceId));

        assertThat(jdbc.queryForObject(
                        "select count(*) from audit_logs where request_id = 'req-select-workspace' "
                                + "and action = 'session.workspace.select' and result = 'SUCCESS'",
                        Integer.class))
                .isEqualTo(1);
    }

    @Test
    void selectingWorkspaceRequiresIdempotencyKey() throws Exception {
        String accessToken = registerAndLogin("idempotency@example.com", "Idempotency User");
        String workspaceId = jdbc.queryForObject("select id::text from workspaces limit 1", String.class);

        mvc.perform(put("/api/v1/session/context/workspace")
                        .header("Authorization", "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"workspaceId\":\"" + workspaceId + "\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("IDEMPOTENCY_KEY_REQUIRED"));
    }

    @Test
    void repeatingWorkspaceSelectionWithSameKeyDoesNotDuplicateAudit() throws Exception {
        String accessToken = registerAndLogin("repeat@example.com", "Repeat User");
        String workspaceId = jdbc.queryForObject("select id::text from workspaces limit 1", String.class);
        String requestBody = "{\"workspaceId\":\"" + workspaceId + "\"}";

        mvc.perform(put("/api/v1/session/context/workspace")
                        .header("Authorization", "Bearer " + accessToken)
                        .header("Idempotency-Key", "same-selection")
                        .header("X-Request-Id", "req-selection-first")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestBody))
                .andExpect(status().isOk());
        mvc.perform(put("/api/v1/session/context/workspace")
                        .header("Authorization", "Bearer " + accessToken)
                        .header("Idempotency-Key", "same-selection")
                        .header("X-Request-Id", "req-selection-retry")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestBody))
                .andExpect(status().isOk());

        assertThat(jdbc.queryForObject(
                        "select count(*) from audit_logs where action = 'session.workspace.select'", Integer.class))
                .isEqualTo(1);
    }

    @Test
    void selectingWorkspaceWithoutMembershipIsForbidden() throws Exception {
        String ownerToken = registerAndLogin("owner@example.com", "Owner User");
        String outsiderToken = registerAndLogin("outsider@example.com", "Outsider User");
        String ownerWorkspaceId = jdbc.queryForObject(
                "select w.id::text from workspaces w join users u on u.id = w.created_by where u.email = 'owner@example.com'",
                String.class);

        mvc.perform(put("/api/v1/session/context/workspace")
                        .header("Authorization", "Bearer " + outsiderToken)
                        .header("Idempotency-Key", "cross-workspace-attempt")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"workspaceId\":\"" + ownerWorkspaceId + "\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("WORKSPACE_ACCESS_DENIED"));

        assertThat(ownerToken).isNotBlank();
    }

    @Test
    void invalidCredentialsUseSameSafeErrorForUnknownAccountAndWrongPassword() throws Exception {
        registerAndLogin("known@example.com", "Known User");

        String unknown = mvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"unknown@example.com\",\"password\":\"WrongPassword\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("INVALID_CREDENTIALS"))
                .andReturn()
                .getResponse()
                .getContentAsString();
        String wrongPassword = mvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"known@example.com\",\"password\":\"WrongPassword\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("INVALID_CREDENTIALS"))
                .andReturn()
                .getResponse()
                .getContentAsString();

        assertThat(objectMapper.readTree(unknown).path("error").path("message").asString())
                .isEqualTo(objectMapper.readTree(wrongPassword).path("error").path("message").asString());
    }

    @Test
    void duplicateRegistrationReturnsConflictInsteadOfServerError() throws Exception {
        registerAndLogin("duplicate@example.com", "First User");

        mvc.perform(post("/api/v1/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"DUPLICATE@example.com\",\"password\":\"StrongPassword-123!\","
                                + "\"displayName\":\"Second User\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("ACCOUNT_ALREADY_EXISTS"));
    }

    @Test
    void refreshTokenRotatesSessionAndRejectsReplay() throws Exception {
        register("refresh@example.com", "Refresh User");
        Tokens original = login("refresh@example.com", "First Device");

        MvcResult refresh = mvc.perform(post("/api/v1/auth/refresh")
                        .header("X-Request-Id", "req-refresh-session")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"" + original.refreshToken() + "\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.accessToken").isNotEmpty())
                .andExpect(jsonPath("$.data.refreshToken").isNotEmpty())
                .andReturn();
        Tokens rotated = tokensFrom(refresh);
        assertThat(rotated.refreshToken()).isNotEqualTo(original.refreshToken());

        mvc.perform(get("/api/v1/account/profile").header("Authorization", "Bearer " + original.accessToken()))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/account/profile").header("Authorization", "Bearer " + rotated.accessToken()))
                .andExpect(status().isOk());
        mvc.perform(post("/api/v1/auth/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"" + original.refreshToken() + "\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("INVALID_REFRESH_TOKEN"));
    }

    @Test
    void logoutRevokesCurrentSessionImmediately() throws Exception {
        register("logout@example.com", "Logout User");
        Tokens tokens = login("logout@example.com", "Logout Device");

        mvc.perform(post("/api/v1/auth/logout")
                        .header("Authorization", "Bearer " + tokens.accessToken())
                        .header("X-Request-Id", "req-logout"))
                .andExpect(status().isNoContent());
        mvc.perform(get("/api/v1/account/profile").header("Authorization", "Bearer " + tokens.accessToken()))
                .andExpect(status().isUnauthorized());

        assertThat(jdbc.queryForObject(
                        "select count(*) from audit_logs where action = 'session.logout' and request_id = 'req-logout'",
                        Integer.class))
                .isEqualTo(1);
    }

    @Test
    void accountCanListAndRevokeAnotherDeviceSession() throws Exception {
        register("devices@example.com", "Devices User");
        Tokens first = login("devices@example.com", "Primary Device");
        Tokens second = login("devices@example.com", "Secondary Device");

        MvcResult list = mvc.perform(get("/api/v1/account/sessions")
                        .header("Authorization", "Bearer " + first.accessToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andReturn();
        String secondarySessionId = null;
        for (var session : objectMapper.readTree(list.getResponse().getContentAsString()).path("data")) {
            if (session.path("deviceName").asString().equals("Secondary Device")) {
                secondarySessionId = session.path("id").asString();
            }
        }
        assertThat(secondarySessionId).isNotBlank();

        mvc.perform(delete("/api/v1/account/sessions/{sessionId}", secondarySessionId)
                        .header("Authorization", "Bearer " + first.accessToken())
                        .header("X-Request-Id", "req-revoke-secondary"))
                .andExpect(status().isNoContent());
        mvc.perform(get("/api/v1/account/profile").header("Authorization", "Bearer " + second.accessToken()))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/account/profile").header("Authorization", "Bearer " + first.accessToken()))
                .andExpect(status().isOk());
    }

    private String registerAndLogin(String email, String displayName) throws Exception {
        register(email, displayName);
        return login(email, null).accessToken();
    }

    private void register(String email, String displayName) throws Exception {
        mvc.perform(post("/api/v1/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"" + email + "\",\"password\":\"StrongPassword-123!\","
                                + "\"displayName\":\"" + displayName + "\"}"))
                .andExpect(status().isCreated());
    }

    private Tokens login(String email, String deviceName) throws Exception {
        String deviceProperty = deviceName == null ? "" : ",\"deviceName\":\"" + deviceName + "\"";
        MvcResult login = mvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"email\":\"" + email + "\",\"password\":\"StrongPassword-123!\""
                                + deviceProperty + "}"))
                .andExpect(status().isOk())
                .andReturn();
        return tokensFrom(login);
    }

    private Tokens tokensFrom(MvcResult result) throws Exception {
        var data = objectMapper.readTree(result.getResponse().getContentAsString()).path("data");
        return new Tokens(data.path("accessToken").asString(), data.path("refreshToken").asString());
    }

    private record Tokens(String accessToken, String refreshToken) {}
}
