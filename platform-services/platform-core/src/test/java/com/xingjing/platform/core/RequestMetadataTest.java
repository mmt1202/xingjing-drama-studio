package com.xingjing.platform.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.UUID;
import org.junit.jupiter.api.Test;

class RequestMetadataTest {
    @Test
    void requiresRequestUserAndWorkspaceIdentifiers() {
        var metadata = new RequestMetadata(
                "req-01",
                UUID.fromString("018f0000-0000-7000-8000-000000000001"),
                UUID.fromString("018f0000-0000-7000-8000-000000000002"));

        assertEquals("req-01", metadata.requestId());
        assertThrows(
                NullPointerException.class,
                () -> new RequestMetadata(null, metadata.userId(), metadata.workspaceId()));
    }

    @Test
    void rejectsBlankRequestIdentifier() {
        var userId = UUID.fromString("018f0000-0000-7000-8000-000000000001");
        var workspaceId = UUID.fromString("018f0000-0000-7000-8000-000000000002");

        assertThrows(IllegalArgumentException.class, () -> new RequestMetadata(" ", userId, workspaceId));
    }
}
