package com.xingjing.platform.identity;

import java.util.UUID;

record SessionPrincipal(UUID userId, UUID sessionId, String email, String displayName) {}
