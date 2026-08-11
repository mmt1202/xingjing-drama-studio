# v9.2 权限与多租户规范

## 1. 授权模型

最终权限由下式计算：

```text
effective = identity permissions
          ∩ workspace membership
          ∩ project/episode/object data scope
          ∩ plan entitlements
          ∩ feature flags
          ∩ object-state policy
          ∩ compliance/billing restrictions
```

任一层拒绝即拒绝。前端只负责展示服务端返回的 capabilities，不负责最终裁决。

## 2. 身份域

| 身份域 | 登录方式 | 作用范围 | 禁止事项 |
|---|---|---|---|
| 创作者用户 | 用户 access/refresh token | 个人和加入的工作区 | 访问平台后台 |
| 后台员工 | 独立后台会话 + MFA | 按后台角色和数据范围 | 使用用户 token 绕过后台审计 |
| 客户审片者 | review link + 验证 + 短期会话 | 单一外链的指定版本 | 成为工作区成员或访问生产接口 |
| API 客户端 | client credential/API key | 显式 scopes | 使用交互式用户权限 |
| 服务身份 | mTLS/短期服务凭证 | 服务到服务 | 以服务身份模拟任意用户操作 |

## 3. 创作者角色矩阵

符号：M 管理，W 编辑/执行，R 只读，A 审批，— 无权。

| 资源/操作 | 所有者 | 项目负责人 | 编剧 | 导演 | 分镜师 | 视觉师 | 视频师 | 音频师 | 剪辑师 | 审核者 | 法务 | 运营 | 客户 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 工作区成员/角色 | M | R | — | — | — | — | — | — | — | — | R | — | — |
| 工作区账单/预算 | M | W | — | R | — | R | R | R | R | — | R | R | — |
| 项目配置/状态 | M | M | R | W | R | R | R | R | R | A | A | R | — |
| 剧本导入/编辑 | M | W | M | W | R | R | R | R | R | A | R | R | — |
| AI 导演设定 | M | W | W | M | W | R | R | R | R | A | R | R | — |
| 主体资产 | M | W | R | A | W | M | W | R | R | A | R | R | — |
| 分镜/故事板 | M | W | R | A | M | W | W | R | R | A | R | R | — |
| 图片生成 | M | W | — | A | W | M | W | — | R | A | R | R | — |
| 视频生成 | M | W | — | A | W | W | M | — | W | A | R | R | — |
| 音频/字幕/对口型 | M | W | R | A | R | R | W | M | W | A | R | R | — |
| 时间线/成片 | M | W | R | A | R | R | W | W | M | A | R | R | — |
| 审片评论 | M | W | W | W | W | W | W | W | W | M | W | W | W |
| 审片批准 | M | A | — | A | — | — | — | — | — | A | A | — | A* |
| 合规记录 | M | W | R | R | R | R | R | R | R | R | M/A | W | — |
| 正式导出 | M | W | — | A | — | — | — | — | W | A | A | W | — |
| 模板/Fork/公开 | M | W | R | W | W | W | W | W | W | A | A | M | — |

`A*` 仅限外链明确授予的客户确认交付，不等同平台内部审批。

## 4. 权限代码

### 工作区与项目

`workspace.view`、`workspace.manage`、`workspace.member.view`、`workspace.member.manage`、`workspace.role.manage`、`workspace.billing.view`、`project.create`、`project.view`、`project.edit`、`project.archive`、`project.member.manage`、`project.budget.manage`。

### 生产

`script.import`、`script.edit`、`script.freeze`、`director.edit`、`asset.create`、`asset.generate`、`asset.approve`、`shot.edit`、`shot.reorder`、`shot.freeze`、`storyboard.generate`、`generation.create`、`generation.cancel`、`generation.select`、`audio.edit`、`timeline.edit`、`render.create`。

### 交付

`review.link.manage`、`review.comment`、`review.approve`、`export.preview`、`export.formal.create`、`publish.package.manage`、`compliance.view`、`compliance.evidence.manage`、`compliance.approve`、`rights.manage`。

### 商业与生态

`billing.view`、`billing.budget.manage`、`template.create`、`template.publish`、`fork.create`、`market.purchase`、`commercial.quote`、`commercial.deliver`、`commercial.accept`、`commercial.settle.view`。

## 5. 团队后台角色

| 操作 | 所有者 | 团队管理员 | 财务 | 项目负责人 | 只读审计 |
|---|---:|---:|---:|---:|---:|
| 成员与邀请 | M | M | R | R | R |
| 角色和权限矩阵 | M | M | — | R | R |
| 席位和套餐 | M | W | R | — | R |
| 账单与算力流水 | M | R | M | 项目范围 R | R |
| 项目成本 | M | R | M | 负责项目 R | R |
| 客户审片链接 | M | M | — | 负责项目 M | R |
| 企业认证与安全 | M | W | R | — | R |
| 操作日志 | M | R | R | 负责项目 R | R |

## 6. 平台后台角色矩阵

| 模块 | 超管 | 平台运营 | 审核员 | 合规法务 | 财务 | 客服 | 模型运营 | 运维 | 商务 | 数据分析 | 只读 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 用户/团队/项目 | M | M | R | R | R | 受限R | R | R | 受限R | R | R |
| 内容审核 | M | R | M | A | — | 受限R | — | R | — | R | R |
| 合规与申诉 | M | R | W | M/A | — | 受限R | — | R | — | R | R |
| 模型与路由 | M | R | R | R | R | — | M | W | — | R | R |
| 任务与队列 | M | R | R | R | R | 受限R | W | M | — | R | R |
| 算力、订单、退款 | M | R | — | R | M/A | 受限W | R | R | R | R | R |
| 模板与商单 | M | M | W | A | W | R | — | R | M | R | R |
| 客服工单 | M | R | R | R | W* | M | R | W* | R | R | R |
| 运营配置/消息 | M | M | R | A* | R | R | R | W | R | R | R |
| 系统安全/员工权限 | M | — | — | R | — | — | — | W | — | R | R |
| 审计日志 | M | 受限R | 受限R | R | 受限R | 受限R | 受限R | R | 受限R | R | R |

`W*` 仅允许执行其职责内、有关联工单或审批的动作。

## 7. 数据范围

数据范围从小到大：`SELF`、`ASSIGNED_OBJECTS`、`PROJECTS`、`WORKSPACE`、`DEPARTMENT`、`PLATFORM_MASKED`、`PLATFORM_FULL`。

- 普通创作者最大为 WORKSPACE。
- 后台员工默认 PLATFORM_MASKED；查看完整敏感字段需要独立权限和业务原因。
- 财务、合规和安全的数据范围按职责拆分，不能因“超级管理员”以外角色获得全字段。
- 导出权限与查看权限分离。

## 8. 多租户隔离

### 应用层

1. gateway 解析身份，但业务服务重新校验 workspace membership。
2. Repository 方法必须显式接受 workspaceId，不提供无租户的通用查询。
3. 通过 ID 读取对象时同时查询 `id + workspace_id`，禁止先读后判断。
4. 事件包含 workspaceId；消费者校验 aggregate 归属。
5. 对象存储键以 workspaceId 开头；签名服务校验调用者和业务对象。

### 数据库层

- 生产启用 RLS，应用连接设置只读事务变量 `app.workspace_id`。
- 平台后台跨租户查询使用独立数据库角色和只读视图；写操作仍调用业务服务。
- 唯一索引包含 workspaceId，避免租户间名称互相占用。

### 缓存与搜索

- Redis key、搜索索引文档和向量集合均包含 workspaceId。
- 不允许使用只含 objectId 的缓存键。
- 权限撤销后失效用户、成员、项目和签名 URL 相关缓存。

## 9. 对象状态策略

- archived：默认只读，只有恢复动作可写。
- frozen：平台冻结，除申诉和证据补充外禁止写。
- compliance blocked：允许修复内容/权利证明，禁止正式导出。
- billing frozen：允许查看和充值/申诉，禁止新付费任务。
- task terminal：终态不可重写；重试创建新 attempt 或新 task。

## 10. 权限变更与审计

- 角色/权限变更使用版本和 `If-Match`。
- 批量提权、导出、退款、封禁、合规放行、模型下线属于高风险动作。
- 高风险动作记录申请、审批人、执行人；申请人和最终审批人按阈值分离。
- 权限修改后立即撤销相关会话能力缓存；已发出的短期媒体 URL 保持尽可能短的有效期。

## 11. 权限测试门禁

- 每个权限代码至少有允许、拒绝、跨租户拒绝三个自动化用例。
- IDOR 测试覆盖所有包含 workspace/project/object ID 的接口。
- 客户外链测试覆盖过期、撤销、暴力尝试、版本不可见和下载禁用。
- 后台测试覆盖数据脱敏、导出分离、审批分离和审计不可删除。
