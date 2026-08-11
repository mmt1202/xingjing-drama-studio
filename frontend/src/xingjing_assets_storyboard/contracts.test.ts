import { describe, expect, it } from "vitest";

import { failedBatchItemIds, moveStableItem } from "./contracts";

describe("资产与分镜稳定标识", () => {
  it("重排镜头时只改变顺序，不改变稳定镜头 ID", () => {
    const shots = [
      { id: "shot-019f-a", sequenceNo: 10 },
      { id: "shot-019f-b", sequenceNo: 20 },
      { id: "shot-019f-c", sequenceNo: 30 },
    ];

    const reordered = moveStableItem(shots, "shot-019f-b", -1);

    expect(reordered.map((shot) => shot.id)).toEqual(["shot-019f-b", "shot-019f-a", "shot-019f-c"]);
    expect(shots.map((shot) => shot.id)).toEqual(["shot-019f-a", "shot-019f-b", "shot-019f-c"]);
  });

  it("安全重试只返回可重试失败项，绝不重复提交已成功项", () => {
    expect(failedBatchItemIds([
      { id: "asset-a", status: "succeeded" },
      { id: "asset-b", status: "failed", retryable: true },
      { id: "asset-c", status: "failed", retryable: false },
      { id: "asset-d", status: "cancelled", retryable: true },
    ])).toEqual(["asset-b", "asset-d"]);
  });
});
