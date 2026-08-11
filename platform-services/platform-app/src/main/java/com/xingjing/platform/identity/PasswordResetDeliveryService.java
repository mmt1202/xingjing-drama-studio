package com.xingjing.platform.identity;

import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Service
class PasswordResetDeliveryService {
    private final RestClient restClient;
    private final String endpoint;
    private final String serviceToken;

    PasswordResetDeliveryService(
            RestClient.Builder builder,
            @Value("${xingjing.identity.notification-url:}") String endpoint,
            @Value("${xingjing.identity.notification-token:}") String serviceToken) {
        this.restClient = builder.build();
        this.endpoint = endpoint.trim();
        this.serviceToken = serviceToken.trim();
    }

    String send(String requestId, String email, String resetToken) {
        if (endpoint.isBlank() || serviceToken.isBlank()) {
            throw new DeliveryUnavailableException();
        }
        try {
            DeliveryResponse response = restClient.post()
                    .uri(endpoint)
                    .header(HttpHeaders.AUTHORIZATION, "Bearer " + serviceToken)
                    .header("X-Request-Id", requestId)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of(
                            "templateKey", "identity.password-reset",
                            "recipient", email,
                            "variables", Map.of("resetToken", resetToken)))
                    .retrieve()
                    .body(DeliveryResponse.class);
            if (response == null || response.messageId() == null || response.messageId().isBlank()) {
                throw new DeliveryUnavailableException();
            }
            return response.messageId();
        } catch (RestClientException error) {
            throw new DeliveryUnavailableException(error);
        }
    }

    record DeliveryResponse(String messageId) {}

    static class DeliveryUnavailableException extends RuntimeException {
        DeliveryUnavailableException() {}

        DeliveryUnavailableException(Throwable cause) {
            super(cause);
        }
    }
}
