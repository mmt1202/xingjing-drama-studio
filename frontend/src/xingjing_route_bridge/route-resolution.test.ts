import { describe, expect, it } from "vitest";

import { resolveAdminRoute, resolveProjectSurface } from "./route-resolution";

describe("xingjing business route resolution", () => {
  it.each([
    ["projects", "project-library"],
    ["script-import", "script-import"],
    ["generation", "generation"],
    ["audio", "audio"],
    ["postproduction", "postproduction"],
    ["compliance-delivery", "compliance-delivery"],
    ["assets-extract", "assets-storyboard"],
    ["shots-quality", "assets-storyboard"],
    ["storyboard", "assets-storyboard"],
  ])("maps %s to a mounted surface", (slug, expected) => {
    expect(resolveProjectSurface(slug)?.kind).toBe(expected);
  });

  it("does not silently map an unknown project surface", () => {
    expect(resolveProjectSurface("does-not-exist")).toBeNull();
  });

  it.each([
    [undefined, "center"],
    ["image", "image"],
    ["image-to-video", "image-to-video"],
    ["text-to-video", "text-to-video"],
    ["video", "video"],
    ["model-center", "model-center"],
    ["model-report", "model-report"],
    ["video-candidates", "video-candidates"],
    ["video-extend", "video-extend"],
    ["video-retry", "video-retry"],
    ["video-stabilize", "video-stabilize"],
    ["video-upscale", "video-upscale"],
  ])("maps the generation child %s to its real workbench view", (itemId, expected) => {
    expect(resolveProjectSurface("generation", itemId)).toMatchObject({
      kind: "generation",
      view: expected,
    });
  });

  it("mounts the task center and rejects unknown generation children", () => {
    expect(resolveProjectSurface("tasks")).toMatchObject({ kind: "generation", view: "tasks" });
    expect(resolveProjectSurface("generation", "unknown")).toBeNull();
  });

  it("resolves an admin slug to the formal route id", () => {
    expect(resolveAdminRoute("admin-users")?.id).toBe("AD-050");
    expect(resolveAdminRoute("unknown")).toBeNull();
  });
});
