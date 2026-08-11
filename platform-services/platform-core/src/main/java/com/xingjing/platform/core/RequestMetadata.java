package com.xingjing.platform.core;

import java.util.Objects;
import java.util.UUID;

public record RequestMetadata(String requestId, UUID userId, UUID workspaceId) {
    public RequestMetadata {
        requestId = Objects.requireNonNull(requestId, "requestId");
        userId = Objects.requireNonNull(userId, "userId");
        workspaceId = Objects.requireNonNull(workspaceId, "workspaceId");
        if (requestId.isBlank()) {
            throw new IllegalArgumentException("requestId must not be blank");
        }
    }
}
