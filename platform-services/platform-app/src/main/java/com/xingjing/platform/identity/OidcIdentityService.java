package com.xingjing.platform.identity;

import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Service
class OidcIdentityService {
    private final RestClient restClient;
    private final String providerKey;
    private final String userInfoEndpoint;

    OidcIdentityService(
            RestClient.Builder builder,
            @Value("${xingjing.identity.oidc.provider-key:}") String providerKey,
            @Value("${xingjing.identity.oidc.userinfo-url:}") String userInfoEndpoint) {
        this.restClient = builder.build();
        this.providerKey = providerKey.trim();
        this.userInfoEndpoint = userInfoEndpoint.trim();
    }

    OidcIdentity verify(String requestedProvider, String accessToken) {
        if (providerKey.isBlank()
                || userInfoEndpoint.isBlank()
                || !providerKey.equals(requestedProvider.trim())
                || accessToken.isBlank()) {
            throw new OidcUnavailableException();
        }
        try {
            @SuppressWarnings("unchecked")
            Map<String, Object> body = restClient.get()
                    .uri(userInfoEndpoint)
                    .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                    .retrieve()
                    .body(Map.class);
            if (body == null) {
                throw new InvalidOidcIdentityException();
            }
            String subject = text(body.get("sub"));
            String email = text(body.get("email")).toLowerCase(java.util.Locale.ROOT);
            String name = optionalText(body.get("name"), email);
            Object verified = body.get("email_verified");
            if (subject.isBlank() || email.isBlank() || !Boolean.TRUE.equals(verified)) {
                throw new InvalidOidcIdentityException();
            }
            return new OidcIdentity(providerKey, subject, email, name);
        } catch (RestClientException error) {
            throw new InvalidOidcIdentityException(error);
        }
    }

    private static String text(Object value) {
        return value instanceof String text ? text.trim() : "";
    }

    private static String optionalText(Object value, String fallback) {
        String text = text(value);
        return text.isBlank() ? fallback : text;
    }

    record OidcIdentity(String providerKey, String subject, String email, String displayName) {}

    static class OidcUnavailableException extends RuntimeException {}

    static class InvalidOidcIdentityException extends RuntimeException {
        InvalidOidcIdentityException() {}

        InvalidOidcIdentityException(Throwable cause) {
            super(cause);
        }
    }
}
