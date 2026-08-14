import { describe, expect, it } from "vitest";

import { createIdempotencyKey } from "./contracts";

describe("audio idempotency key", () => {
  it("reuses the key for one submission and changes it for a new submission", () => {
    const first = createIdempotencyKey("project-1", "generate-audio", "submission-a");
    expect(createIdempotencyKey("project-1", "generate-audio", "submission-a")).toBe(first);
    expect(createIdempotencyKey("project-1", "generate-audio", "submission-b")).not.toBe(first);
  });
});
