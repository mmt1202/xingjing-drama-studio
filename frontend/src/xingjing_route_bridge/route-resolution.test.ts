import { describe, expect, it } from "vitest";

import { resolveAdminRoute, resolveProjectSurface } from "./route-resolution";

describe("xingjing business route resolution", () => {
  it.each([
    ["projects", "project-library"],
    ["script-import", "script-import"],
    ["generation", "generation"],
    ["audio", "audio"],
    ["postproduction", "postproduction"],
    ["compliance-delivery", "postproduction"],
    ["assets-extract", "assets-storyboard"],
    ["shots-quality", "assets-storyboard"],
    ["storyboard", "assets-storyboard"],
  ])("maps %s to a mounted surface", (slug, expected) => {
    expect(resolveProjectSurface(slug)?.kind).toBe(expected);
  });

  it("does not silently map an unknown project surface", () => {
    expect(resolveProjectSurface("does-not-exist")).toBeNull();
  });

  it("resolves an admin slug to the formal route id", () => {
    expect(resolveAdminRoute("admin-users")?.id).toBe("AD-050");
    expect(resolveAdminRoute("unknown")).toBeNull();
  });
});
