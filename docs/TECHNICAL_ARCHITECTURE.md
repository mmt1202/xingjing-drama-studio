# 星镜剧创 AI 短剧 / 漫剧生产平台技术可行性与项目架构文档

> 文档类型：技术可行性分析 / 技术项目架构需求分析  
> 文档版本：v3.0 整合版  
> 更新时间：2026-06-29  
> 项目名称：星镜剧创  
> 英文代号：Xingjing Drama Studio / XJ Drama  
> 产品定位：面向中国 AI 短剧 / 漫剧团队的生产操作系统  
> 技术总原则：从第一天按 Spring Cloud 微服务架构规划，服务边界一次性分析清楚；开发优先级可以分阶段，但架构规划不模糊。

---

# 1. 文档目的

本文件用于在正式开发星镜剧创项目之前，把技术方向、服务边界、系统架构、数据边界、AI 接入、消息队列、任务状态、部署方式、后期扩展和技术风险一次性分析清楚。

本文件不是简单的技术栈清单，而是可以指导研发、招聘、分工、排期、仓库初始化、数据库建模、接口设计、部署规划和后续架构演进的技术可行性需求分析文档。

核心目标：

1. 明确星镜剧创为什么必须按生产级系统设计，而不是 Demo 工具设计。
2. 明确 Java 后端、Python AI 大模型、前端工作台、平台后台之间的职责边界。
3. 明确后端从第一天采用 Spring Cloud 微服务架构，而不是先单体后大改。
4. 明确所有目标服务边界，避免后期大量返工。
5. 明确每个服务的数据所有权、接口职责、事件职责和可扩展方向。
6. 明确 AI 长任务、模型路由、算力计费、合规留痕、媒体处理的技术设计。
7. 明确平台运营管理后台的技术服务体系。

---

# 2. 技术判断总结

## 2.1 项目本质判断

星镜剧创不是普通 AI 生图 / 生视频网站。

普通 AI 工具站的流程通常是：

```text
用户输入提示词
↓
调用模型
↓
生成图片或视频
↓
用户下载
```

星镜剧创的流程是：

```text
用户 / 团队 / 项目 / 剧集
↓
小说 / 剧本导入
↓
AI 剧本解析
↓
AI 导演理解
↓
主体资产提取
↓
角色 / 场景 / 道具资产管理
↓
智能分镜
↓
故事板
↓
多模型图片 / 视频 / 音频生成
↓
任务队列 / 失败重试 / 算力计费
↓
配音 / 字幕 / 对口型
↓
审片协作 / 版本管理
↓
成片合成 / 媒体处理
↓
合规留痕 / 授权记录
↓
多平台发布包导出
↓
平台运营后台监管、审核、计费、客服、数据分析
```

所以它本质是：

> AI 内容生产操作系统 + 短剧项目管理系统 + 多模型任务编排系统 + 媒体资产管理系统 + 团队协作系统 + 合规交付系统 + 平台运营管理系统。

## 2.2 技术选型结论

最终推荐技术路线：

```text
前端：Next.js + React + TypeScript + Tailwind CSS + shadcn/ui
后台管理前端：React / Next.js + TypeScript + Ant Design Pro / Arco Design
后端：Java 21 + Spring Boot 3.x + Spring Cloud Alibaba
注册配置：Nacos Discovery + Nacos Config
网关：Spring Cloud Gateway
服务调用：OpenFeign
权限认证：Spring Security / OAuth2 / JWT / RBAC
数据库：PostgreSQL 优先，MySQL 备选
缓存：Redis
消息队列：RabbitMQ 起步，RocketMQ / Kafka 作为后期大规模选项
AI 服务：Python 3.11+ + FastAPI + 自研大模型 + 第三方模型 Adapter
任务执行：Java 任务调度服务 + Python AI Worker + Media Worker
对象存储：MinIO 开发环境，阿里云 OSS / 腾讯云 COS 生产环境
媒体处理：FFmpeg + 独立 Media Worker
实时通信：SSE 起步，WebSocket 支持协作和审片
部署：Docker Compose 开发 / 测试，Kubernetes 生产后期
监控：Prometheus + Grafana + Loki / ELK + SkyWalking
文档：OpenAPI / Knife4j / Markdown 技术文档
```

## 2.3 架构原则

1. 从第一天使用 Spring Cloud 微服务底座。
2. 服务边界按目标架构完整拆清楚。
3. 开发阶段可以 P0 / P1 / P2 分期实现，但服务和领域边界必须一次性规划。
4. Java 负责平台业务系统，Python 负责 AI 能力和模型生成。
5. 所有 AI 长任务必须异步化，不允许用普通 HTTP 长连接硬等。
6. 所有生成任务必须进入任务表和消息队列。
7. 所有模型调用必须留痕，包括模型、参数、提示词、输入、输出、成本。
8. 所有媒体文件必须进入对象存储，数据库只保存元数据。
9. 所有计费必须采用预估、冻结、结算、退回机制。
10. 所有导出必须经过版本记录和合规检查。
11. 所有服务必须从数据所有权上明确归属，避免互相直接改库。
12. 所有跨服务交互优先通过 API 或事件，不允许业务耦合到数据库。
13. 平台后台不允许直接改业务库，必须通过业务服务接口。
14. 所有后台敏感操作必须有审计日志。
15. 产品可以先做 P0，但架构必须支撑团队版、企业版、私有化、API 和平台运营。

---

# 3. 技术路线说明

## 3.1 为什么不用单体架构

星镜剧创未来必然包含以下业务域：

```text
用户认证
团队空间
权限系统
项目系统
剧集系统
剧本系统
AI 导演系统
主体资产系统
分镜系统
故事板系统
提示词系统
生成任务系统
模型路由系统
媒体处理系统
算力计费系统
订单支付系统
审片协作系统
版本管理系统
合规审计系统
发布包系统
模板系统
Fork 二创系统
商单系统
数据看板系统
管理后台系统
客服工单系统
运营配置系统
```

如果使用单体架构，后期会遇到：

1. 业务代码膨胀。
2. AI 任务、计费、权限、导出耦合。
3. 大文件处理拖垮主服务。
4. 视频生成任务影响普通 API。
5. 团队协作和审片实时能力难扩展。
6. 后期拆服务成本非常高。
7. 企业私有化和分模块部署困难。
8. 不利于多人并行开发。

## 3.2 为什么选择 Java Spring Cloud

| 维度 | Java Spring Cloud | NestJS |
|---|---|---|
| 国内招聘 | 强 | 中 |
| B 端系统 | 强 | 可以 |
| 企业化 | 强 | 中 |
| 私有化部署 | 强 | 可以 |
| 权限计费 | 成熟 | 可以 |
| 服务治理 | 强 | 中 |
| 团队长期维护 | 强 | 中 |
| AI 能力 | 仍需 Python | 仍需 Python |
| 与项目规划匹配 | 更高 | 次选 |

## 3.3 Python 的位置

AI 大模型底座已经是 Python，这正好适合做 AI 服务层。

最终分工：

```text
Java = 平台业务、权限、计费、项目、任务、审计、导出、运营后台
Python = 自研大模型、剧本解析、分镜生成、提示词生成、模型调用、媒体 AI 处理
Next.js = 创作者生产工作台
React / Ant Design Pro = 平台运营管理后台
```

---

# 4. 总体技术架构

## 4.1 总体链路

```text
用户 Web / 桌面端 / 移动端
        ↓
Next.js 创作者生产工作台
        ↓
Spring Cloud Gateway
        ↓
Java 微服务业务中台
        ↓
RabbitMQ / RocketMQ / Redis / PostgreSQL / Object Storage
        ↓
Python AI Service + AI Workers + Media Workers
        ↓
自研大模型 + 第三方模型供应商

平台内部员工
        ↓
Admin Web 后台前端
        ↓
admin-service 后台聚合服务
        ↓
后台专属服务 + 业务服务后台接口
        ↓
审计日志 / 数据看板 / 运营配置 / 客服工单 / 审核合规
```

## 4.2 分层架构

```text
客户端层
  ├── Web 生产工作台
  ├── Windows 桌面端
  ├── macOS 桌面端
  ├── iOS 审片端
  ├── Android 审片端
  ├── HarmonyOS 审片端
  └── Admin Web 平台后台

网关层
  └── Spring Cloud Gateway

认证与组织层
  ├── auth-service
  ├── user-service
  ├── workspace-service
  └── permission-service

项目生产层
  ├── project-service
  ├── episode-service
  ├── script-service
  ├── director-service
  ├── asset-service
  ├── shot-service
  ├── storyboard-service
  └── prompt-service

AI 生成层
  ├── generation-service
  ├── model-service
  ├── Python ai-service
  ├── Python ai-worker
  └── model-adapter

媒体与交付层
  ├── media-service
  ├── audio-service
  ├── export-service
  └── publish-package-service

协作与治理层
  ├── review-service
  ├── version-service
  ├── compliance-service
  ├── rights-service
  └── audit-log-service

商业化层
  ├── billing-service
  ├── order-service
  ├── template-service
  ├── fork-service
  └── commercial-service

平台后台层
  ├── admin-service
  ├── staff-service
  ├── admin-permission-service
  ├── operation-service
  ├── support-service
  ├── platform-config-service
  ├── feature-flag-service
  ├── content-review-service
  └── admin-export-service

基础设施层
  ├── Nacos
  ├── PostgreSQL
  ├── Redis
  ├── RabbitMQ / RocketMQ
  ├── MinIO / OSS / COS
  ├── FFmpeg
  ├── Prometheus
  ├── Grafana
  ├── Loki / ELK
  └── SkyWalking
```

---

# 5. 微服务拆分规划

## 5.1 业务服务总览

| 服务 | 中文名 | 核心职责 | 优先级 |
|---|---|---|---|
| gateway-service | 网关服务 | 统一入口、路由、鉴权、限流 | P0 |
| auth-service | 认证服务 | 登录、Token、第三方登录 | P0 |
| user-service | 用户服务 | 用户资料、账号状态、个人设置 | P0 |
| workspace-service | 工作区服务 | 团队、成员、席位、邀请 | P0 |
| permission-service | 权限服务 | RBAC、项目权限、资源权限 | P0 |
| project-service | 项目服务 | 短剧项目、项目配置、项目状态 | P0 |
| episode-service | 剧集服务 | 剧集、集数、单集状态、单集成本 | P0 |
| script-service | 剧本服务 | 剧本导入、分集、版本、原文映射 | P0 |
| director-service | AI导演服务 | 导演设定、风格、节奏、生成策略 | P0 |
| asset-service | 主体资产服务 | 角色、场景、道具、服装、声音资产 | P0 |
| shot-service | 分镜服务 | 分镜生成、编辑、排序、导入导出 | P0 |
| storyboard-service | 故事板服务 | 故事板卡片、首尾帧、生成候选 | P0 |
| prompt-service | 提示词服务 | 提示词模板、变量、版本、成功案例 | P0 |
| generation-service | 生成任务服务 | 任务创建、状态、重试、回调、结果 | P0 |
| model-service | 模型中心服务 | 模型供应商、模型能力、成本、路由配置 | P0 |
| media-service | 媒体服务 | 文件元数据、转码、视频处理任务 | P0 |
| audio-service | 音频字幕服务 | 配音、音色、字幕、BGM、音效 | P0 |
| review-service | 审片协作服务 | 评论、打回、审批、外链审片 | P1 |
| version-service | 版本服务 | 剧本、资产、分镜、故事板、成片版本 | P1 |
| billing-service | 算力计费服务 | 余额、冻结、扣费、退回、流水 | P0 |
| order-service | 订单支付服务 | 套餐、充值、支付、退款、发票 | P1 |
| export-service | 导出服务 | MP4、素材包、字幕、分镜表、归档包 | P0 |
| publish-package-service | 发布包服务 | 多平台发布包、封面、标题、简介、标签 | P1 |
| compliance-service | 合规服务 | AIGC 标识、模型记录、内容风险、审核 | P1 |
| rights-service | 版权授权服务 | IP、素材、真人形象、商用授权 | P1 |
| template-service | 模板服务 | 项目模板、角色模板、分镜模板、风格模板 | P1 |
| fork-service | Fork 服务 | 项目 Fork、授权、署名、分成 | P2 |
| commercial-service | 商单服务 | 商单、接单、交付、验收、结算 | P2 |
| analytics-service | 数据看板服务 | 生产效率、成本、模型成功率、团队人效 | P1 |
| notification-service | 通知服务 | 站内信、邮件、短信、任务完成通知 | P1 |
| ai-service | AI 能力服务 | 自研模型、剧本解析、分镜、提示词、模型调用 | P0 |
| ai-worker | AI 任务 Worker | 消费生成任务、调用模型、回写结果 | P0 |
| media-worker | 媒体 Worker | FFmpeg、合成、转码、导出包打包 | P0 |

## 5.2 后台专属服务总览

| 服务 | 中文名 | 职责 | 优先级 |
|---|---|---|---|
| admin-service | 后台聚合服务 | 后台入口、菜单、聚合查询、操作转发 | P0 |
| staff-service | 后台员工服务 | 员工账号、后台登录、员工状态 | P0 |
| admin-permission-service | 后台权限服务 | 后台角色、菜单权限、操作权限 | P0 |
| operation-service | 运营配置服务 | Banner、公告、活动、教程、推荐配置 | P1 |
| support-service | 客服工单服务 | 工单、回复、转交、状态流转 | P1 |
| platform-config-service | 平台配置服务 | 系统开关、上传限制、功能开关 | P0 |
| feature-flag-service | 功能开关服务 | 灰度、实验、模块开关 | P1 |
| audit-log-service | 审计日志服务 | 后台操作日志、敏感操作记录 | P0 |
| content-review-service | 内容审核服务 | 审核队列、审核记录、风险命中 | P1 |
| admin-export-service | 后台导出服务 | 后台报表、列表、审计导出 | P1 |

---

# 6. 核心服务职责与数据边界

## 6.1 gateway-service

职责：API 路由、JWT 校验、请求限流、跨域配置、API 版本路由、灰度路由预留、请求日志、文件上传前置限制、SSE / WebSocket 路由转发。

不负责：业务权限细节、用户资料、具体业务逻辑、业务数据库访问。

## 6.2 auth-service

职责：手机号验证码登录、邮箱登录、第三方登录预留、JWT、Refresh Token、Token 黑名单、密码重置、登录日志、风险登录检测、多设备登录管理。

数据所有权：

```text
auth_account
auth_login_log
auth_refresh_token
auth_oauth_account
auth_password_reset
auth_security_event
```

## 6.3 user-service

职责：用户基础资料、头像、昵称、偏好设置、默认工作区、账号状态、隐私设置、账号注销流程、实名认证预留。

数据所有权：

```text
user_profile
user_setting
user_preference
user_privacy
user_status_log
```

## 6.4 workspace-service

职责：个人工作区、团队工作区、成员邀请、成员加入 / 退出、席位、团队套餐绑定、工作区切换、成员角色绑定、团队账单归属、团队操作日志。

数据所有权：

```text
workspace
workspace_member
workspace_invite
workspace_seat
workspace_role_binding
workspace_billing_setting
workspace_operation_log
```

## 6.5 permission-service

模型：RBAC + ABAC + Resource Scope。

权限层级：platform、workspace、project、episode、shot、asset、storyboard、export_package。

数据所有权：

```text
permission_definition
role_definition
role_permission
resource_acl
user_role_binding
permission_audit_log
```

## 6.6 project-service

职责：创建项目、项目配置、项目类型、目标平台、画幅比例、项目预算、项目状态、项目成员、归档、删除、统计聚合。

数据所有权：

```text
project
project_setting
project_member
project_status_log
project_archive
project_statistics_snapshot
```

## 6.7 episode-service

职责：剧集、自动分集结果、手动调整集数、单集状态、单集目标时长、单集成本、单集导出状态、审片状态、排序、锁定。

数据所有权：

```text
episode
episode_setting
episode_status_log
episode_cost_snapshot
episode_review_status
```

## 6.8 script-service

职责：文本导入、文件导入、剧本清洗、章节识别、分集、旁白 / 对白识别、原文映射、版本管理、AI 改写触发、内容体检、风险初筛、预计成片时长。

数据所有权：

```text
script
script_version
script_episode_mapping
script_chapter
script_character_hint
script_scene_hint
script_import_record
script_parse_result
script_risk_result
```

AI 调用链路：script-service 保存原文，generation-service 创建解析任务，Python ai-worker 执行解析，script-service 接收结构化结果并入库。

## 6.9 director-service

职责：全局导演设定、单集导演设定、受众定位、视觉风格、镜头语言、角色表演、音频方向、平台节奏、成本策略、模型推荐、版本和影响范围。

数据所有权：

```text
director_profile
director_profile_version
director_style_preset
director_episode_profile
director_impact_record
```

## 6.10 asset-service

资产类型：CHARACTER、CHARACTER_STATE、SCENE、PROP、COSTUME、VOICE、BRAND_ASSET、WORLD_ELEMENT、ANIMAL、VEHICLE、MONSTER。

职责：主体提取结果入库、手动新增资产、参考图组、三视图、多状态图、声音绑定、授权状态、资产引用、跨项目复用、版本管理、一致性 ID、删除影响分析。

数据所有权：

```text
asset
asset_version
asset_reference_image
asset_state
asset_voice_binding
asset_usage
asset_rights_status
asset_consistency_profile
```

## 6.11 shot-service

职责：AI 分镜结果入库、手动新增分镜、编辑、删除、复制、合并、拆分、排序、批量修改、导入导出、质量检测、状态管理、锁定。

数据所有权：

```text
shot
shot_version
shot_asset_reference
shot_status_log
shot_import_record
shot_quality_check
```

## 6.12 storyboard-service

职责：分镜卡片、图片提示词引用、视频提示词引用、参考主体、首帧、尾帧、图片候选、视频候选、已选素材、配音引用、字幕引用、确认状态、排序、评论入口。

数据所有权：

```text
storyboard
storyboard_item
storyboard_asset_reference
storyboard_selected_media
storyboard_status_log
```

## 6.13 prompt-service

职责：图片提示词、视频提示词、负向提示词、模板变量、风格模板、主体变量注入、模型专用适配、成功案例、失败分析、版本回滚、提示词审核。

数据所有权：

```text
prompt
prompt_version
prompt_template
prompt_variable
prompt_style_preset
prompt_model_adapter
prompt_case_library
prompt_quality_score
```

## 6.14 generation-service

定位：Java 业务系统和 Python AI 服务之间的任务中枢。

职责：创建生成任务、校验权限和参数、请求 billing-service 预估和冻结算力、写任务表、投递 MQ、接收回调、更新状态、记录结果、结算计费、通知、失败重试、取消任务、批量任务、任务优先级、错误归因。

任务类型：

```text
SCRIPT_ANALYZE
DIRECTOR_GENERATE
ASSET_EXTRACT
ASSET_IMAGE_GENERATE
SHOT_GENERATE
PROMPT_IMAGE_GENERATE
PROMPT_VIDEO_GENERATE
IMAGE_GENERATE
IMAGE_EDIT
VIDEO_TEXT_TO_VIDEO
VIDEO_IMAGE_TO_VIDEO
VIDEO_FIRST_LAST_FRAME
AUDIO_TTS
SUBTITLE_GENERATE
LIP_SYNC
FINAL_RENDER
EXPORT_PACKAGE
COMPLIANCE_CHECK
```

数据所有权：

```text
generation_task
generation_task_log
generation_batch
generation_result
generation_callback
generation_retry_record
generation_error_record
```

## 6.15 model-service

职责：模型供应商、模型能力、模型价格、模型健康状态、模型可用区域、排队状态、成功率、质量评分、路由规则、降级策略、备案信息、供应商凭证管理、调用限流。

数据所有权：

```text
model_provider
model_definition
model_capability
model_pricing
model_health_status
model_route_rule
model_fallback_rule
model_credential_ref
model_filing_info
model_quality_stat
```

## 6.16 media-service

职责：上传签名、下载签名、文件元数据、图片预览图、视频缩略图、视频低清预览版、文件归属、文件访问鉴权、文件生命周期、转码任务、防盗链、CDN 预留。

数据所有权：

```text
media_file
media_variant
media_usage
media_upload_record
media_transcode_task
media_access_log
```

## 6.17 audio-service

职责：旁白配音、角色对白、角色音色、音色绑定、TTS 任务、声音克隆预留、音效、BGM、字幕、SRT / ASS、音频混合。

数据所有权：

```text
voice_profile
character_voice_binding
audio_clip
tts_task
subtitle
subtitle_segment
bgm_asset
sound_effect
audio_mix_record
```

## 6.18 review-service

职责：分镜评论、故事板评论、视频时间点评论、图片候选评论、成片评论、打回原因、审批通过、状态流转、@ 成员、客户外链审片。

数据所有权：

```text
review_thread
review_comment
review_status
review_external_link
review_approval_record
review_attachment
```

## 6.19 version-service

职责：核心对象版本快照、备注、对比、回滚、锁定、归档、来源追踪、差异记录。

数据所有权：

```text
version_snapshot
version_diff
version_restore_record
version_lock_record
```

## 6.20 billing-service

职责：算力账户、余额、预估、冻结、扣减、退回、流水、项目成本、单集成本、镜头成本、模型成本、团队账单、平台毛利统计。

数据所有权：

```text
credit_account
credit_transaction
credit_freeze_record
credit_pricing_rule
project_cost
episode_cost
shot_cost
model_cost_stat
```

## 6.21 order-service

职责：套餐管理、算力包购买、会员订阅、团队版套餐、企业版订单、支付回调、退款、发票、优惠券、订单状态。

数据所有权：

```text
plan
order
payment_record
refund_record
invoice
coupon
subscription
```

## 6.22 export-service

职责：MP4、素材包、图片、视频、音频、字幕、分镜表、项目归档、剪映草稿、导出历史、导出失败重试。

数据所有权：

```text
export_task
export_file
export_history
export_format_config
export_error_record
```

## 6.23 publish-package-service

职责：不同平台发布包、封面、标题、简介、标签、平台规格检查、平台安全区检查。

数据所有权：

```text
publish_package
publish_platform_profile
publish_title
publish_description
publish_tag
publish_cover
publish_check_result
```

## 6.24 compliance-service

职责：AIGC 标识、显式水印策略、隐式标识记录、模型调用清单、提示词记录、内容风险检查、导出前合规检查、合规报告、审计归档。

数据所有权：

```text
compliance_record
aigc_label_record
model_usage_record
content_risk_result
compliance_checklist
compliance_report
audit_log
```

## 6.25 rights-service

职责：IP 授权记录、素材来源、真人形象授权、品牌授权、音频授权、商用授权、授权文件、过期提醒、禁止商用标记、授权风险提示。

数据所有权：

```text
rights_record
rights_file
ip_authorization
portrait_authorization
brand_authorization
audio_authorization
commercial_license
rights_risk
```

## 6.26 template-service

职责：项目模板、剧本模板、分镜模板、资产模板、角色模板、场景模板、提示词模板、视频风格模板、字幕模板、发布包模板。

数据所有权：

```text
template
template_version
template_category
template_usage
template_review
template_price
template_revenue_share
```

## 6.27 fork-service

职责：项目 Fork、Fork 权限、Fork 范围、原作者署名、商用授权、收费 Fork、分成 Fork、来源追踪、版本快照。

数据所有权：

```text
fork_record
fork_permission
fork_scope
fork_revenue_rule
fork_source_snapshot
```

## 6.28 commercial-service

职责：商单发布、接单、报价、样片交付、成片交付、发布包交付、验收、结算、评价、争议处理预留。

数据所有权：

```text
commercial_order
commercial_bid
commercial_delivery
commercial_acceptance
commercial_settlement
commercial_review
```

## 6.29 analytics-service

职责：项目数、剧集数、分镜数、图片生成统计、视频生成统计、模型成功率、平均耗时、算力消耗、返工次数、成片分钟数、团队人效、审片通过率、发布包导出率、合规完整率。

数据所有权：

```text
analytics_event
analytics_snapshot
project_metric
team_metric
model_metric
cost_metric
```

## 6.30 notification-service

职责：站内通知、邮件通知、短信通知、任务完成通知、任务失败通知、审片 @ 通知、账单提醒、算力不足提醒、导出完成通知、SSE / WebSocket 推送。

数据所有权：

```text
notification
notification_template
notification_channel
notification_read_status
notification_send_log
```

---

# 7. 平台后台专属服务设计

## 7.1 admin-service

定位：后台聚合入口。

职责：后台菜单聚合、首页数据聚合、用户数据聚合、项目数据聚合、任务数据聚合、财务数据聚合、操作权限校验、操作转发、数据导出请求、敏感操作二次确认。

admin-service 不直接管理业务数据，不绕过业务服务操作数据库。

后台操作映射：

| 后台操作 | 实际服务 |
|---|---|
| 禁用用户 | user-service |
| 冻结项目 | project-service |
| 重试任务 | generation-service |
| 退回算力 | billing-service |
| 下架模板 | template-service |
| 审核内容 | content-review-service / compliance-service |
| 查看模型状态 | model-service |
| 处理订单 | order-service |
| 处理工单 | support-service |
| 发送通知 | notification-service |
| 修改系统开关 | platform-config-service |

## 7.2 staff-service

职责：后台员工账号、后台登录、员工状态、员工角色绑定、员工部门、员工联系方式、员工登录日志、员工二次验证、访问范围控制、员工禁用。

数据所有权：

```text
admin_staff
admin_staff_role_binding
admin_staff_login_log
admin_staff_security_setting
admin_staff_access_scope
```

## 7.3 admin-permission-service

模型：Admin RBAC + Data Scope + Sensitive Action Approval。

职责：后台角色、后台菜单权限、后台按钮权限、后台接口权限、后台数据范围、敏感操作权限、审批权限、权限变更记录。

数据所有权：

```text
admin_role
admin_permission
admin_role_permission
admin_menu
admin_data_scope
admin_sensitive_action
admin_permission_change_log
```

## 7.4 operation-service

职责：首页 Banner、新手引导、官方教程、官方案例、推荐模板、热门风格、活动配置、邀请码、兑换码、新人礼包、限时算力包、模型体验活动、创作者扶持计划。

数据所有权：

```text
operation_banner
operation_guide
operation_tutorial
operation_activity
operation_invite_code
operation_redeem_code
operation_recommendation
operation_exposure_log
operation_click_log
```

## 7.5 support-service

职责：工单创建、工单分配、客服回复、内部备注、转交处理、状态流转、关联用户、关联项目、关联任务、关联订单、满意度评价。

工单类型：

```text
generation_failed
credit_issue
payment_failed
export_failed
account_issue
permission_issue
model_quality_issue
content_review_issue
rights_complaint
invoice_issue
commercial_dispute
other
```

数据所有权：

```text
support_ticket
support_ticket_message
support_ticket_internal_note
support_ticket_assignment
support_ticket_status_log
support_ticket_satisfaction
```

## 7.6 platform-config-service

职责：平台注册开关、免费试用开关、文件上传限制、默认水印策略、强制 AIGC 标识、默认导出规则、内容审核阈值、算力价格参数、队列并发限制、模型默认开关、接口调用限制。

数据所有权：

```text
platform_config
platform_config_group
platform_config_version
platform_config_change_log
```

## 7.7 feature-flag-service

职责：功能开关、用户灰度、团队灰度、套餐灰度、实验配置、新功能测试、功能下线。

数据所有权：

```text
feature_flag
feature_flag_rule
feature_flag_target
feature_flag_experiment
feature_flag_log
```

## 7.8 audit-log-service

职责：后台登录、用户禁用、项目冻结、任务重试、算力调整、订单退款、模型配置修改、套餐修改、模板上下架、内容审核、合规放行、权限变更、系统配置变更、员工权限变更等审计。

审计字段：

```text
audit_id
operator_id
operator_role
operation_type
target_type
target_id
before_data
after_data
reason
ip
user_agent
request_id
created_at
```

要求：审计日志不可物理删除，不可被普通管理员修改，支持按操作人、时间、对象、类型查询，支持高危操作前后数据对比。

## 7.9 content-review-service

审核对象：script、prompt、image、video、audio、subtitle、cover、final_video、template、fork_project、commercial_delivery。

状态：pending、machine_passed、machine_suspected、manual_passed、manual_rejected、need_material、export_blocked、public_blocked。

数据所有权：

```text
review_item
review_result
review_rule_hit
review_manual_action
review_status_log
review_blocklist
```

## 7.10 admin-export-service

支持导出：用户列表、团队列表、项目列表、任务列表、算力流水、订单报表、退款报表、模型成本、审核记录、审计日志、工单报表。

要求：大数据量异步导出、文件有效期、敏感数据脱敏、权限控制、导出操作写审计日志。

---

# 8. Java 与 Python 通信架构

## 8.1 同步调用

适合短任务：剧本片段分析、提示词生成、导演建议生成、质量评分、风险提示。

流程：

```text
Java 服务
↓ REST
Python ai-service
↓
返回结构化结果
↓
Java 入库
```

接口示例：

```text
POST /ai/script/analyze
POST /ai/director/generate
POST /ai/asset/extract
POST /ai/shot/generate
POST /ai/prompt/image
POST /ai/prompt/video
```

## 8.2 异步调用

适合长任务：批量生图、批量视频、配音、对口型、成片合成、导出包。

流程：

```text
前端请求
↓
Java generation-service 创建任务
↓
billing-service 冻结算力
↓
任务写入 MQ
↓
Python ai-worker 消费
↓
调用模型
↓
结果上传对象存储
↓
回调 Java
↓
Java 更新任务状态
↓
billing-service 结算
↓
notification-service 通知前端
```

## 8.3 回调协议

```json
{
  "task_id": "task_xxx",
  "status": "success",
  "result_files": [],
  "model_call": {},
  "cost_detail": {},
  "error": null
}
```

---

# 9. 消息队列与事件架构

## 9.1 队列选型

第一阶段使用 RabbitMQ，原因是 Spring 集成简单、支持死信队列、支持延迟重试、适合 AI 任务分发、运维难度低。

后期可升级 RocketMQ 或 Kafka。

## 9.2 队列规划

```text
generation.script.queue
generation.asset.queue
generation.image.queue
generation.video.queue
generation.audio.queue
generation.lipsync.queue
media.render.queue
export.package.queue
compliance.check.queue
notification.queue
admin.export.queue
support.notification.queue
```

## 9.3 核心事件

```text
ProjectCreatedEvent
ScriptImportedEvent
ScriptParsedEvent
AssetExtractedEvent
AssetGeneratedEvent
ShotGeneratedEvent
StoryboardCreatedEvent
GenerationTaskCreatedEvent
GenerationTaskSucceededEvent
GenerationTaskFailedEvent
CreditFrozenEvent
CreditChargedEvent
CreditRefundedEvent
FinalVideoRenderedEvent
ExportPackageCreatedEvent
ComplianceCheckPassedEvent
ComplianceCheckFailedEvent
ReviewCommentCreatedEvent
ReviewApprovedEvent
UserDisabledEvent
ProjectFrozenEvent
ModelDisabledEvent
OrderRefundedEvent
SupportTicketCreatedEvent
AdminOperationLoggedEvent
```

## 9.4 失败处理

每类任务都需要最大重试次数、死信队列、错误归因、失败通知、算力退回、手动重试入口、替代模型策略和后台可查队列日志。

---

# 10. 数据库架构

## 10.1 数据库选型

推荐 PostgreSQL。

原因：

1. 关系数据强。
2. JSONB 适合 AI 参数。
3. pgvector 可支持项目知识库。
4. 适合复杂审计记录。
5. 适合 SaaS 多租户。

## 10.2 Schema 规划

业务 Schema：

```text
auth_schema
user_schema
workspace_schema
permission_schema
project_schema
episode_schema
script_schema
director_schema
asset_schema
shot_schema
storyboard_schema
prompt_schema
generation_schema
model_schema
media_schema
audio_schema
review_schema
version_schema
billing_schema
order_schema
export_schema
publish_package_schema
compliance_schema
rights_schema
template_schema
fork_schema
commercial_schema
analytics_schema
notification_schema
```

后台 Schema：

```text
admin_schema
staff_schema
admin_permission_schema
operation_schema
support_schema
platform_config_schema
feature_flag_schema
audit_log_schema
content_review_schema
admin_export_schema
```

## 10.3 数据访问原则

1. 每个服务只能直接写自己的表。
2. 跨服务读写通过 API 或事件。
3. analytics-service 可通过只读库 / 数据仓库读取。
4. admin-service 不直接改业务表。
5. 审计日志不可物理删除。
6. 计费流水不可物理删除。
7. 后台导出走 admin-export-service。
8. 敏感数据默认脱敏。

---

# 11. 对象存储架构

## 11.1 文件类型

```text
剧本文档
角色参考图
角色定妆图
角色三视图
场景图
道具图
分镜图
视频片段
配音文件
字幕文件
成片视频
封面图
素材包
发布包
合规报告
成本报表
后台导出报表
授权文件
```

## 11.2 存储方案

```text
开发环境：MinIO
生产环境：阿里云 OSS / 腾讯云 COS
```

## 11.3 路径规范

```text
/workspaces/{workspace_id}/
  projects/{project_id}/
    scripts/
    assets/
      characters/
      scenes/
      props/
      voices/
    episodes/{episode_id}/
      shots/{shot_id}/
        images/
        videos/
        audio/
        subtitles/
    final/
    exports/
    compliance/
    reports/

/admin/
  exports/
  audit-reports/
  finance-reports/
  review-attachments/
```

## 11.4 文件访问

私有桶、临时签名 URL、权限校验后签发下载、CDN 加速预留、防盗链、文件生命周期策略、项目逻辑删除后异步清理、授权文件和合规文件长期保留。

---

# 12. 媒体处理架构

## 12.1 media-worker 职责

```text
视频转码
视频拼接
字幕烧录
音频混合
音量调整
BGM 淡入淡出
封面抽帧
视频比例转换
预览低清版
高清最终版
素材包打包
剪映草稿导出
```

## 12.2 成片合成流程

```text
export-service 创建成片任务
↓
media-service 校验素材
↓
任务进入 media.render.queue
↓
media-worker 下载视频 / 音频 / 字幕
↓
FFmpeg 合成
↓
生成预览版
↓
生成高清版
↓
上传对象存储
↓
回调 export-service
```

---

# 13. AI 模型路由架构

## 13.1 模型能力抽象

```text
text.generate
script.analyze
director.generate
asset.extract
shot.generate
prompt.image
prompt.video
image.generate
image.edit
video.text_to_video
video.image_to_video
video.first_last_frame
video.lip_sync
audio.tts
audio.sfx
subtitle.generate
moderation.check
```

## 13.2 模型路由因素

```text
任务类型
项目类型
画面风格
是否真人
是否漫剧
是否需要角色一致性
是否需要首尾帧
预算
质量档位
生成速度
模型当前状态
历史成功率
用户偏好
平台策略
失败重试策略
后台禁用 / 限流策略
```

## 13.3 模型调用记录

```text
model_call_id
task_id
provider
model_name
model_version
input_prompt
input_params
reference_assets
output_files
cost
duration
status
error_code
created_at
```

## 13.4 自研模型定位

自研模型负责：

```text
小说/剧本解析
剧情推理
人物关系
分集规划
AI导演理解
主体提取
智能分镜
提示词生成
一致性检查
短剧节奏优化
```

第三方模型负责：

```text
图片生成
图生视频
文生视频
首尾帧视频
对口型
TTS
声音克隆
高清修复
```

最终结构：

```text
自研模型 = 编剧 / 导演 / 分镜 / 策划中枢
第三方模型 = 图像 / 视频 / 音频执行器
Java 后端 = 生产流程 / 权限 / 计费 / 审计系统
```

---

# 14. 算力计费技术设计

## 14.1 计费流程

```text
用户点击生成
↓
generation-service 计算任务
↓
model-service 返回模型价格
↓
billing-service 预估消耗
↓
billing-service 冻结算力
↓
任务进入队列
↓
AI Worker 执行
↓
成功：billing-service 正式扣费
失败：billing-service 释放或退回
```

## 14.2 计费状态

```text
estimated
frozen
charged
released
refunded
compensated
```

## 14.3 账单维度

```text
用户
团队
项目
剧集
分镜
模型
任务类型
成员
时间
平台成本
平台毛利
```

## 14.4 关键规则

1. 排队中不正式扣费。
2. 成功后正式扣费。
3. 失败后退回。
4. 第三方模型已扣但失败，要按平台策略补偿。
5. 用户取消任务要根据执行阶段判断退费。
6. 批量任务按子任务结算。
7. 所有流水不可删除。
8. 后台人工调整必须记录操作原因和审计日志。

---

# 15. 合规与审计技术设计

## 15.1 合规记录

系统必须记录：

```text
剧本来源
素材来源
提示词
模型供应商
模型名称
模型版本
生成时间
操作人
生成结果
AIGC 标识策略
授权状态
导出版本
目标平台
后台放行人
后台审核记录
```

## 15.2 AIGC 标识

支持显式水印、片尾标识、导出包说明、元数据记录、合规报告记录。

## 15.3 导出前检查

```text
是否存在未确认镜头
是否存在未完成任务
是否存在未授权素材
是否有 AIGC 标识记录
是否有模型调用记录
是否存在真人形象风险
是否存在敏感内容风险
是否符合平台规格
是否被后台禁止导出
```

## 15.4 审计日志

所有关键操作写审计日志，包括登录、创建项目、导入剧本、修改剧本、生成资产、生成视频、修改分镜、删除资产、导出成片、合规放行、充值、扣费、权限变更、成员移除、后台登录、用户禁用、项目冻结、模型禁用、套餐改价、订单退款、人工退费、内容审核。

---

# 16. 实时通信架构

## 16.1 SSE

用于生成任务进度、导出任务进度、队列状态、算力变化、任务失败提醒、后台任务告警。

## 16.2 WebSocket

用于多人审片、实时评论、在线状态、@ 通知、后台告警推送、协同编辑预留。

路线：

```text
P0：SSE
P1：WebSocket 审片和后台告警
P2：协同编辑
```

---

# 17. 前端技术架构

## 17.1 创作者前台技术栈

```text
Next.js
React
TypeScript
Tailwind CSS
shadcn/ui
TanStack Query
Zustand
TanStack Table
dnd-kit
Video.js / 自定义播放器
SSE
WebSocket
```

## 17.2 平台后台技术栈

```text
React / Next.js
TypeScript
Ant Design Pro 或 Arco Design
TanStack Query
Zustand
ECharts
ProTable / 高级表格
权限路由
```

---

# 18. 仓库结构建议

```text
xingjing-drama-studio/
│
├── README.md
├── docs/
│   ├── PRODUCT_REQUIREMENTS.md
│   ├── TECHNICAL_ARCHITECTURE.md
│   ├── API_DESIGN.md
│   ├── DATABASE_DESIGN.md
│   ├── DEPLOYMENT.md
│   └── research/
│
├── apps/
│   ├── web/
│   └── admin-web/
│
├── services/
│   ├── gateway-service/
│   ├── auth-service/
│   ├── user-service/
│   ├── workspace-service/
│   ├── permission-service/
│   ├── project-service/
│   ├── episode-service/
│   ├── script-service/
│   ├── director-service/
│   ├── asset-service/
│   ├── shot-service/
│   ├── storyboard-service/
│   ├── prompt-service/
│   ├── generation-service/
│   ├── model-service/
│   ├── media-service/
│   ├── audio-service/
│   ├── review-service/
│   ├── version-service/
│   ├── billing-service/
│   ├── order-service/
│   ├── export-service/
│   ├── publish-package-service/
│   ├── compliance-service/
│   ├── rights-service/
│   ├── template-service/
│   ├── fork-service/
│   ├── commercial-service/
│   ├── analytics-service/
│   ├── notification-service/
│   ├── admin-service/
│   ├── staff-service/
│   ├── admin-permission-service/
│   ├── operation-service/
│   ├── support-service/
│   ├── platform-config-service/
│   ├── feature-flag-service/
│   ├── audit-log-service/
│   ├── content-review-service/
│   └── admin-export-service/
│
├── ai/
│   ├── ai-service/
│   ├── ai-workers/
│   ├── model-adapters/
│   ├── prompt-engine/
│   └── agents/
│
├── workers/
│   ├── media-worker/
│   └── export-worker/
│
├── packages/
│   ├── api-contracts/
│   ├── shared-types/
│   ├── ui-kit/
│   └── sdk/
│
├── infra/
│   ├── docker/
│   ├── nacos/
│   ├── postgres/
│   ├── redis/
│   ├── rabbitmq/
│   ├── minio/
│   ├── nginx/
│   └── k8s/
│
└── scripts/
```

---

# 19. 部署架构

## 19.1 开发环境

```text
Docker Compose
Nacos
PostgreSQL
Redis
RabbitMQ
MinIO
Gateway
Java services
Python ai-service
Python workers
Next.js web
Admin web
```

## 19.2 测试环境

```text
单机或多机 Docker Compose
Nginx
HTTPS
独立测试数据库
独立对象存储
日志采集
基础监控
```

## 19.3 生产环境早期

```text
云服务器 + Docker Compose
云 PostgreSQL
云 Redis
云对象存储
自建 RabbitMQ
Nginx
HTTPS
```

## 19.4 生产环境后期

```text
Kubernetes
Nacos 集群
PostgreSQL 主从
Redis 集群
RabbitMQ / RocketMQ 集群
对象存储
Prometheus
Grafana
SkyWalking
Loki / ELK
CI/CD
```

---

# 20. 开发阶段规划

## 20.1 P0 技术底座

```text
Gateway
Nacos
Auth
User
Workspace
Permission
Project
Episode
Script
Generation
Billing
Model
Media
Export
Admin
Staff
Admin Permission
Audit Log
AI Service
PostgreSQL
Redis
RabbitMQ
MinIO
```

## 20.2 P0 生产闭环

```text
剧本导入
AI 解析
主体提取
资产生成
分镜生成
故事板
图片生成
视频生成
配音字幕
成片合成
MP4 导出
算力扣费
任务状态推送
后台基础管理
```

## 20.3 P1 工作室版

```text
审片协作
版本管理
对口型
发布包
合规中心
版权授权
数据看板
模板中心
剪映草稿
客户外链审片
客服工单
内容审核
订单财务
运营配置
```

## 20.4 P2 平台生态版

```text
Fork
商单中心
模板市场
资产市场
API / SDK
私有化部署
多平台发布 API
数据回流
企业 SSO
开放平台
企业客户后台
```

---

# 21. 技术风险与应对

| 风险 | 说明 | 应对 |
|---|---|---|
| 微服务数量多 | 开发复杂度高 | 服务边界明确，P0 只实现核心接口 |
| Java/Python 协作复杂 | 跨语言通信 | REST + MQ + 明确回调协议 |
| AI 任务耗时长 | 用户等待 | 队列 + SSE + 通知 |
| 第三方模型不稳定 | 成功率不稳定 | Model Router + 失败重试 + fallback |
| 算力计费争议 | 用户投诉 | 冻结 / 成功扣费 / 失败退回 |
| 媒体文件巨大 | 存储和带宽压力 | 对象存储 + CDN + 生命周期 |
| 分布式事务复杂 | 服务间一致性 | 状态机 + 事件驱动，少用强事务 |
| 合规后补困难 | 商业化风险 | 从第一版记录模型、提示词、素材、标识 |
| 角色一致性不足 | 作品质量差 | 主体资产库 + 参考图 + 自研模型理解 |
| 过早 K8s | 运维压力 | Docker Compose 起步，生产规模后上 K8s |
| 数据库拆太早 | 运维复杂 | 单实例多 schema，服务数据边界先清晰 |
| 权限设计不细 | 团队版难做 | permission-service 和 admin-permission-service 独立规划 |
| 后台直接改库 | 审计和一致性风险 | admin-service 只调用业务服务 API |
| 后台高危操作误操作 | 用户损失和财务风险 | 二次确认 + 审批 + 审计日志 |

---

# 22. 技术验收标准

## 22.1 架构验收

1. 所有服务可通过 Nacos 注册。
2. 所有外部请求经过 Gateway。
3. 服务间调用通过 OpenFeign 或事件。
4. 每个服务有独立配置。
5. 每个服务有清晰数据边界。
6. Python AI 服务可独立部署。
7. Worker 可独立扩容。
8. 后台服务与前台业务服务边界清晰。

## 22.2 任务验收

1. 所有生成任务有 task_id。
2. 所有任务有状态。
3. 所有任务可重试。
4. 所有任务失败有错误原因。
5. 所有长任务通过 MQ。
6. 前端可实时看到任务状态。
7. 后台可查看全平台任务状态和失败原因。

## 22.3 计费验收

1. 生成前可预估。
2. 任务开始前冻结。
3. 成功后扣费。
4. 失败后退回。
5. 流水不可删除。
6. 项目级成本可统计。
7. 后台人工调整必须记录审计。

## 22.4 合规验收

1. 模型调用有记录。
2. 提示词有记录。
3. 生成结果有记录。
4. 素材来源可记录。
5. AIGC 标识策略可记录。
6. 导出操作有审计日志。
7. 合规报告可生成。
8. 后台可阻断高风险发布包导出。

## 22.5 后台验收

1. 后台员工可以独立登录。
2. 后台登录与前台用户登录隔离。
3. 后台角色权限可配置。
4. 不同后台角色看到不同菜单。
5. 无权限操作被拦截。
6. 高危操作需要二次确认。
7. 所有敏感操作写日志。
8. 审计日志可按时间、操作人、对象查询。
9. 审计日志不可删除。
10. 后台可管理用户、团队、项目、任务、模型、算力、订单、审核、合规、模板、客服、运营配置。

---

# 23. 最终技术定案

星镜剧创的技术架构定案为：

```text
Spring Cloud Alibaba 微服务体系
+
Java 业务中台
+
Python AI 大模型服务
+
RabbitMQ / RocketMQ 异步任务系统
+
PostgreSQL 数据库
+
Redis 缓存
+
MinIO / OSS 对象存储
+
FFmpeg 媒体处理
+
Next.js 创作者生产工作台
+
Admin Web 平台运营管理后台
+
SSE / WebSocket 实时状态
+
合规审计与算力计费系统
```

核心原则：

> 从第一天采用完整微服务架构规划，服务边界一次性分析清楚；开发优先级可以分阶段，但架构不偷懒。

最终目标：

> 支撑星镜剧创从 P0 的“剧本到第一集成片”生产闭环，逐步扩展到工作室协作、企业私有化、多模型生产、合规交付、模板生态、Fork 二创、商单平台和平台运营管理后台。
