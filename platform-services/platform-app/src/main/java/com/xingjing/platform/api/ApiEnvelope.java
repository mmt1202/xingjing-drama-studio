package com.xingjing.platform.api;

import java.time.Instant;

public record ApiEnvelope<T>(T data, Meta meta) {
    public static <T> ApiEnvelope<T> success(T data, String requestId) {
        return new ApiEnvelope<>(data, new Meta(requestId, Instant.now()));
    }

    public record Meta(String requestId, Instant serverTime) {}
}
