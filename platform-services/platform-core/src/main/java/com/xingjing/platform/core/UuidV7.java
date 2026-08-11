package com.xingjing.platform.core;

import java.security.SecureRandom;
import java.time.Clock;
import java.util.UUID;

/** Generates RFC 9562 version 7 UUIDs for externally visible aggregate identifiers. */
public final class UuidV7 {
    private static final SecureRandom RANDOM = new SecureRandom();

    private UuidV7() {}

    public static UUID randomUuid() {
        return randomUuid(Clock.systemUTC());
    }

    static UUID randomUuid(Clock clock) {
        long timestamp = clock.millis() & 0x0000ffffffffffffL;
        long randomA = RANDOM.nextInt(1 << 12) & 0x0fffL;
        long randomB = RANDOM.nextLong() & 0x3fff_ffff_ffff_ffffL;
        long mostSignificantBits = (timestamp << 16) | 0x7000L | randomA;
        long leastSignificantBits = 0x8000_0000_0000_0000L | randomB;
        return new UUID(mostSignificantBits, leastSignificantBits);
    }
}
