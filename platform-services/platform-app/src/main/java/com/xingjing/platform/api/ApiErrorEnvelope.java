package com.xingjing.platform.api;

import java.time.Instant;
import java.util.List;

public record ApiErrorEnvelope(Error error, ApiEnvelope.Meta meta) {
    public static ApiErrorEnvelope unauthenticated(String requestId) {
        return new ApiErrorEnvelope(
                new Error("UNAUTHENTICATED", "登录态无效或已过期", List.of(), false),
                new ApiEnvelope.Meta(requestId, Instant.now()));
    }

    public static ApiErrorEnvelope of(String code, String message, boolean retryable, String requestId) {
        return new ApiErrorEnvelope(
                new Error(code, message, List.of(), retryable),
                new ApiEnvelope.Meta(requestId, Instant.now()));
    }

    public record Error(String code, String message, List<Object> details, boolean retryable) {}
}
