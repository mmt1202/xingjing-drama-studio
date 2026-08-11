package com.xingjing.platform.identity;

import com.xingjing.platform.core.UuidV7;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Locale;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
class LoginSecurityEventService {
    private final JdbcTemplate jdbc;

    LoginSecurityEventService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    void record(
            String requestId,
            String email,
            UUID userId,
            boolean success,
            String reason,
            String ipAddress,
            String deviceName) {
        String normalizedEmail = email.trim().toLowerCase(Locale.ROOT);
        jdbc.update(
                "insert into login_security_events "
                        + "(id,request_id,user_id,identifier_hash,result,reason,ip_address,region,device_name,occurred_at) "
                        + "values (?,?,?,?,?,?,?,?,?,?)",
                UuidV7.randomUuid(),
                requestId,
                userId,
                TokenHasher.sha256(normalizedEmail),
                success ? "SUCCESS" : "FAILED",
                reason,
                ipAddress,
                null,
                deviceName == null || deviceName.isBlank() ? "unknown" : deviceName.trim(),
                Timestamp.from(Instant.now()));
    }
}
