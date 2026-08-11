# v9.2 AI 模型接入与路由规范

## 1. 边界

Java generation-service 创建和管理业务任务、权限、预算、算力与最终状态。Python ai-service 负责能力编排、供应商适配、结构化输出校验和模型调用证据。供应商不能直接成为业务真相源。

## 2. 首期能力矩阵

| capability | 输入 | 输出 | P0最低能力 | 主要质量指标 |
|---|---|---|---|---|
| `script.analyze` | 长文本、章节 | 人物、场景、情节、风险结构 | 分段、续跑、人工覆盖保留 | 抽取准确率、结构完整率 |
| `director.generate` | 剧本、平台、风格 | 导演设定、节奏、镜头语言 | JSON schema 输出 | 人工采纳率 |
| `subject.extract` | 剧本/章节 | 角色、场景、道具候选 | 合并、去重、来源映射 | 召回率、重复率 |
| `shot.generate` | 剧本版本、导演设定 | 镜头列表 | 稳定 shot draft ID、时长约束 | 人工修改率 |
| `prompt.generate` | 镜头、资产引用 | 图/视频提示词 | 模板变量、负面词、可解释 | 生成成功率 |
| `image.generate` | prompt、参考图 | 图片候选 | 多候选、seed、比例、参考强度 | 主体一致性、可用率 |
| `image.edit` | 图片、蒙版、指令 | 修复图片 | 局部重绘、高清、去噪 | 区域遵循、伪影率 |
| `video.generate` | prompt、首尾帧、参考 | 视频候选 | 图生/文生、时长、比例 | 角色稳定、动作遵循、闪烁率 |
| `audio.tts` | 文本、音色、情绪 | 音频 | 中文、多角色、时间估计 | 可懂度、情绪、时长偏差 |
| `subtitle.align` | 音频、文本 | 字幕 cue | 毫秒时间、SRT/ASS | 对齐误差 |
| `lip_sync` | 视频、音频、人脸区域 | 对口型视频 | 失败保留原视频 | 口型同步、人脸稳定 |
| `content.review` | 文本/媒体摘要 | 风险标签与证据 | 规则版本、风险等级 | 误杀率、漏检率 |
| `media.enhance` | 图/视频 | 超分、去闪、补帧 | 可取消、保留原版 | 清晰度、伪影率 |

## 3. 候选供应商登记

下表来自现有研究材料，只作为接入候选和 POC 范围，不代表已签约或已核验最新 API：

| 候选 | 能力槽位 | 使用定位 | 准入前必须核验 |
|---|---|---|---|
| 星镜自研模型 | 长文理解、改编、主体/分镜、路由、风险规则 | 必须掌控的垂直能力 | 结构化输出、吞吐、版本和部署方式 |
| 火山引擎相关模型能力 | 文本、图片、视频、语音候选 | 中国区生产能力候选 | 商用授权、API 可用性、价格、备案和回调 |
| Vidu | 参考生视频、图生视频候选 | 角色一致性视频候选 | 参考图限制、时长、并发、成本和失败码 |
| 可灵 | 图片/视频生成候选 | 高质量镜头候选 | 企业 API、配额、内容政策、回调和成本 |
| Runway | 视频生成海外候选 | 海外或精品镜头备选 | 地域、合规、结算、数据跨境和 SLA |
| MiniMax/海螺相关能力 | 视频/语音候选 | 多模型补充 | 企业 API、音色授权、并发和成本 |

正式接入清单由模型 POC 评审产生，必须记录 providerCode、合同主体、数据区域、能力、单价、限流、SLA、内容政策、密钥负责人和下线方案。

## 4. 统一任务协议

```json
{
  "taskId": "019b...",
  "capability": "video.generate",
  "input": {
    "prompt": "...",
    "negativePrompt": "...",
    "references": [
      {"type": "character", "assetVersionId": "019b...", "signedUrl": "short-lived"}
    ],
    "firstFrame": {"assetId": "019b..."},
    "durationMs": 5000,
    "aspectRatio": "9:16"
  },
  "constraints": {
    "qualityMode": "standard",
    "maxProviderCost": 1200,
    "deadlineMs": 600000,
    "allowFallback": true
  },
  "context": {
    "workspaceId": "019b...",
    "projectId": "019b...",
    "shotId": "019b...",
    "inputDigest": "sha256..."
  }
}
```

输出：

```json
{
  "provider": "provider-code",
  "model": "model-code",
  "modelVersion": "provider-version",
  "providerTaskId": "external-id",
  "status": "success",
  "results": [
    {"mediaType": "video/mp4", "objectKey": "...", "sha256": "...", "durationMs": 5000}
  ],
  "usage": {"providerUnit": "seconds", "providerQuantity": 5, "providerCostMinor": 860},
  "evidence": {"seed": 1234, "requestDigest": "...", "responseDigest": "..."}
}
```

## 5. 适配器接口

每个 provider adapter 实现：

- `validate(capability,input,constraints)`：返回字段级问题和供应商限制。
- `estimate(...)`：返回供应商成本区间、预计时长和不确定性。
- `submit(...)`：返回 providerTaskId 和初始状态。
- `poll(...)`：无可靠回调时查询状态。
- `cancel(...)`：返回 accepted、unsupported 或 alreadyFinished。
- `parseCallback(headers,body)`：校验并标准化回调。
- `fetchResult(...)`：拉取、校验并上传平台对象存储。
- `normalizeError(...)`：映射统一失败码。

供应商特有参数放入 `providerOptions`，公共页面不得直接依赖特有字段。

## 6. 路由

路由顺序：

1. 按 capability 和硬约束过滤不兼容模型。
2. 按项目类型、真人/漫剧、参考能力和平台合规过滤。
3. 按质量档位、预算上限、预计时延计算候选分。
4. 按最近成功率、错误率、队列深度和熔断状态动态调整。
5. 应用用户允许的模型范围和指定偏好。

参考评分：

```text
score = quality*0.35 + consistency*0.25 + reliability*0.20
      + latency*0.10 + costEfficiency*0.10 - compliancePenalty
```

精品重点镜头可提高质量权重；批量过场可提高成本和时延权重。路由决策必须保存候选、得分、淘汰原因和最终模型。

## 7. 重试与降级

- 参数/素材错误：不重试，要求用户修复。
- 429/短暂 5xx：同模型指数退避一次或两次。
- 稳定供应商错误：切同能力备用模型，重新估价并检查剩余冻结额。
- 视频多次失败：允许降级为静帧运镜方案，但必须用户确认，不能静默改变交付类型。
- 合规拒绝：不自动改写规避；进入合规处理。
- 切换模型后保留全部尝试和成本，不覆盖失败证据。

## 8. 统一错误码

| 代码 | 含义 | 自动重试 |
|---|---|---|
| `AI_INVALID_INPUT` | 参数或输入媒体不合法 | 否 |
| `AI_UNSUPPORTED_CAPABILITY` | 模型不支持能力组合 | 否，重新路由 |
| `AI_PROVIDER_RATE_LIMIT` | 供应商限流 | 是 |
| `AI_PROVIDER_UNAVAILABLE` | 供应商不可用 | 是/切换 |
| `AI_PROVIDER_TIMEOUT` | 上游超时 | 有条件 |
| `AI_CONTENT_REJECTED` | 内容政策拒绝 | 否 |
| `AI_RESULT_INVALID` | 结果缺失、损坏或摘要不符 | 是/切换 |
| `AI_BUDGET_EXCEEDED` | 预计成本超预算 | 否 |
| `AI_CANCEL_UNSUPPORTED` | 供应商无法取消 | 否，继续追踪 |
| `AI_UNKNOWN_PROVIDER_ERROR` | 未归类错误 | 一次后人工分析 |

## 9. POC 准入门禁

每个模型在生产开放前使用固定基准集验证：

- 50 个文本/结构化样本；
- 30 个角色、20 个场景、100 个图片镜头；
- 50 个图生视频、30 个文生视频、20 个首尾帧镜头；
- 10 个角色音色、100 条对白；
- 正常、限流、超时、违规、回调重复、结果损坏等异常。

输出成功率、P50/P95 耗时、人工可用率、主体一致性、成本、错误映射覆盖率和降级效果。未通过回调验签、成本采集或结果摘要校验的模型不得开放付费生产。

## 10. 数据与隐私

- 发送给供应商的文本和媒体遵循项目数据策略，记录供应商、区域和用途。
- 不向供应商传递平台用户密码、联系方式、账务或无关项目数据。
- 日志保存摘要和受限原始证据引用，不在普通日志保存完整敏感提示词和媒体 URL。
- 用户删除或合同要求删除时，按供应商能力执行删除请求并保存回执。
