import { describe, expect, it } from "vitest";
import { applyTaskSnapshot, createIdempotencyKey, taskStatusLabel } from "./contracts";

describe("生成任务状态投影", () => {
  it("取消请求后忽略迟到的成功回调并保留取消终态", () => {
    const cancelling = { id: "task-1", status: "cancelling", version: 3 } as const;
    expect(applyTaskSnapshot(cancelling, { id: "task-1", status: "succeeded", version: 2 })).toEqual(cancelling);
  });

  it("接受服务端更新版本并展示完整处理中状态", () => {
    expect(applyTaskSnapshot({ id: "t", status: "queued", version: 1 }, { id: "t", status: "running", version: 2 }).status).toBe("running");
    expect(taskStatusLabel("queued")).toBe("排队中");
    expect(taskStatusLabel("running")).toBe("运行中");
    expect(taskStatusLabel("cancelling")).toBe("取消中");
    expect(taskStatusLabel("failed")).toBe("失败");
  });
});

describe("幂等键", () => {
  it("同一次提交复用键，不同提交生成不同键", () => {
    const first = createIdempotencyKey("project-1", "generate-video", "submission-a");
    expect(createIdempotencyKey("project-1", "generate-video", "submission-a")).toBe(first);
    expect(createIdempotencyKey("project-1", "generate-video", "submission-b")).not.toBe(first);
  });
});
