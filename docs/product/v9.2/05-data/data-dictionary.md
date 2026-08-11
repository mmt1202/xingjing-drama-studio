# v9.2 PostgreSQL 数据字典

## 1. 全局约定

### 类型

- 主键：`uuid`，UUIDv7。
- 时间：`timestamptz`，UTC。
- 金额/算力：`bigint` 最小单位。
- 状态：受 CHECK 约束的 `varchar(32)`；高频稳定枚举可升级为 PostgreSQL enum。
- 半结构化供应商参数：`jsonb`，必须有 schema 版本。
- 媒体摘要：`char(64)` SHA-256。

### 通用字段组

`AUDITABLE`：`created_at timestamptz not null`、`created_by uuid`、`updated_at timestamptz not null`、`updated_by uuid`、`version bigint not null default 1`。

`TENANT`：`workspace_id uuid not null`，外键指向 `workspace.workspaces(id)`；平台域表除外。

`SOFT_DELETE`：`deleted_at timestamptz null`、`deleted_by uuid null`；唯一约束使用 `WHERE deleted_at IS NULL` 部分索引。

## 2. auth schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `auth.users` | `id`、`public_no varchar(32)`、`display_name varchar(80)`、`avatar_object_key text`、`status varchar(24)`、`locale varchar(16)`、`timezone varchar(40)`、AUDITABLE、SOFT_DELETE | `public_no` 唯一；status∈active,disabled,cancelling,anonymized |
| `auth.user_credentials` | `id`、`user_id`、`type varchar(20)`、`identifier_hash char(64)`、`secret_hash text`、`verified_at`、`failed_count int`、`locked_until`、AUDITABLE | `(type,identifier_hash)` 唯一；secret 不存明文 |
| `auth.login_sessions` | `id`、`user_id`、`token_hash char(64)`、`device_id`、`ip inet`、`user_agent text`、`expires_at`、`revoked_at`、`last_seen_at` | `token_hash` 唯一；索引 `(user_id,expires_at desc)` |
| `auth.verification_challenges` | `id`、`purpose`、`target_hash`、`code_hash`、`attempts`、`expires_at`、`consumed_at` | 索引 `(purpose,target_hash,expires_at)`；消费一次 |
| `auth.security_events` | `id`、`user_id`、`event_type`、`risk_level`、`ip`、`device_id`、`detail jsonb`、`occurred_at` | 索引 `(user_id,occurred_at desc)`；追加写 |

## 3. workspace schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `workspace.workspaces` | `id`、`type`、`name`、`owner_user_id`、`status`、`plan_id`、`enterprise_profile_id`、AUDITABLE、SOFT_DELETE | type∈personal,team,enterprise；索引 owner |
| `workspace.workspace_members` | `id`、TENANT、`user_id`、`member_status`、`joined_at`、`invited_by`、AUDITABLE、SOFT_DELETE | 有效 `(workspace_id,user_id)` 唯一 |
| `workspace.roles` | `id`、TENANT、`code`、`name`、`is_system bool`、`data_scope`、AUDITABLE | 有效 `(workspace_id,code)` 唯一 |
| `workspace.permissions` | `id`、`code`、`resource`、`action`、`risk_level` | `code` 平台唯一 |
| `workspace.role_permissions` | `role_id`、`permission_id`、`condition jsonb` | 复合主键 |
| `workspace.member_roles` | `member_id`、`role_id`、`project_id null` | `(member_id,role_id,coalesce(project_id,...))` 唯一 |
| `workspace.invitations` | `id`、TENANT、`target_hash`、`role_ids uuid[]`、`token_hash`、`expires_at`、`accepted_at`、`revoked_at`、`invited_by` | token 唯一；到期/撤销不可接受 |
| `workspace.seats` | `id`、TENANT、`seat_type`、`assigned_member_id`、`starts_at`、`ends_at`、`status` | 同一成员同类型有效席位唯一 |
| `workspace.enterprise_profiles` | `id`、TENANT、`legal_name`、`registration_no_hash`、`invoice_profile jsonb`、`verification_status`、`verified_at` | TENANT 一对一；敏感字段加密 |

## 4. project schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `project.projects` | `id`、TENANT、`project_no`、`name`、`project_type`、`narrative_mode`、`target_platforms text[]`、`aspect_ratio`、`style_preset`、`quality_mode`、`budget_cap bigint`、`status`、`current_version_no bigint`、AUDITABLE、SOFT_DELETE | `(workspace_id,project_no)` 唯一；索引 status/updated_at |
| `project.project_members` | `id`、TENANT、`project_id`、`member_id`、`role_code`、`data_scope jsonb`、AUDITABLE | 有效 `(project_id,member_id)` 唯一 |
| `project.episodes` | `id`、TENANT、`project_id`、`episode_no int`、`title`、`duration_target_ms bigint`、`budget_cap bigint`、`status`、AUDITABLE、SOFT_DELETE | 有效 `(project_id,episode_no)` 唯一 |
| `project.project_status_logs` | `id`、TENANT、`project_id`、`from_status`、`to_status`、`reason`、`actor_id`、`occurred_at` | 追加写；索引 `(project_id,occurred_at)` |
| `project.project_snapshots` | `id`、TENANT、`project_id`、`version_no`、`content_digest char(64)`、`manifest jsonb`、`frozen_at`、`frozen_by` | `(project_id,version_no)` 唯一；digest 索引 |

## 5. content schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `content.scripts` | `id`、TENANT、`project_id`、`source_type`、`title`、`current_version_id`、AUDITABLE、SOFT_DELETE | 一个项目可多剧本；current version 同 script |
| `content.script_versions` | `id`、TENANT、`script_id`、`version_no`、`content jsonb`、`source_object_key`、`content_digest`、`status`、`parent_version_id`、`created_by`、`created_at` | `(script_id,version_no)` 唯一；冻结后不可改 |
| `content.source_mappings` | `id`、TENANT、`script_version_id`、`target_type`、`target_id`、`source_locator jsonb`、`confidence numeric(5,4)` | 索引 target；confidence 0..1 |
| `content.director_profiles` | `id`、TENANT、`project_id`、`version_no`、`audience`、`style jsonb`、`rhythm jsonb`、`camera_language jsonb`、`status`、AUDITABLE | `(project_id,version_no)` 唯一 |
| `content.assets` | `id`、TENANT、`project_id`、`asset_type`、`name`、`tags text[]`、`current_version_id`、`rights_status`、AUDITABLE、SOFT_DELETE | `(project_id,asset_type,name)` 有效唯一 |
| `content.asset_versions` | `id`、TENANT、`asset_id`、`version_no`、`object_key`、`sha256`、`metadata jsonb`、`source_type`、`source_record_id`、`status`、`created_at`、`created_by` | `(asset_id,version_no)` 唯一；sha256 索引 |
| `content.asset_references` | `id`、TENANT、`asset_version_id`、`target_type`、`target_id`、`usage_type`、`override_config jsonb`、AUDITABLE | `(asset_version_id,target_type,target_id,usage_type)` 唯一 |
| `content.shots` | `id`、TENANT、`project_id`、`episode_id`、`shot_no`、`sequence_no numeric(12,4)`、`current_version_id`、`status`、AUDITABLE、SOFT_DELETE | `(episode_id,shot_no)` 唯一；索引 sequence |
| `content.shot_versions` | `id`、TENANT、`shot_id`、`version_no`、`shot_size`、`camera_move`、`duration_ms`、`dialogue`、`narration`、`sound_cues jsonb`、`config jsonb`、`status`、`created_at`、`created_by` | `(shot_id,version_no)` 唯一 |
| `content.storyboards` | `id`、TENANT、`shot_id`、`shot_version_id`、`selected_image_id`、`selected_video_id`、`status`、AUDITABLE | 当前 shot version 最多一个有效故事板 |
| `content.prompts` | `id`、TENANT、`storyboard_id`、`prompt_type`、`template_id`、`rendered_text`、`negative_text`、`variables jsonb`、`version_no`、AUDITABLE | `(storyboard_id,prompt_type,version_no)` 唯一 |

## 6. generation schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `generation.model_providers` | `id`、`code`、`name`、`status`、`credential_ref`、`region`、`callback_secret_ref`、AUDITABLE | code 唯一；只保存密钥引用 |
| `generation.model_definitions` | `id`、`provider_id`、`model_code`、`model_version`、`capability`、`limits jsonb`、`pricing_rule jsonb`、`quality_score`、`status`、AUDITABLE | `(provider_id,model_code,model_version)` 唯一 |
| `generation.generation_tasks` | `id`、TENANT、`task_no`、`project_id`、`episode_id`、`shot_id`、`task_type`、`input jsonb`、`input_digest`、`status`、`priority`、`idempotency_key`、`credit_hold_id`、`progress`、`result_summary jsonb`、`failure_code`、`queued_at`、`started_at`、`finished_at`、AUDITABLE | `(workspace_id,idempotency_key)` 唯一；状态/时间索引 |
| `generation.model_call_records` | `id`、TENANT、`task_id`、`attempt_no`、`provider_id`、`model_id`、`provider_task_id`、`request_digest`、`response_summary jsonb`、`status`、`provider_cost bigint`、`started_at`、`finished_at`、`error_code`、`raw_log_object_key` | `(task_id,attempt_no)` 唯一；供应商任务 ID 索引；追加写 |
| `generation.generated_assets` | `id`、TENANT、`task_id`、`media_type`、`object_key`、`sha256`、`width`、`height`、`duration_ms`、`metadata jsonb`、`selected bool`、`created_at` | `(task_id,sha256)` 唯一 |
| `generation.outbox_events` | `id`、`aggregate_type`、`aggregate_id`、`event_type`、`schema_version`、`payload jsonb`、`created_at`、`published_at`、`attempts` | 未发布索引；只追加 |
| `generation.event_consumer_receipts` | `consumer_name`、`event_id`、`processed_at`、`result_digest` | 复合主键，消费者幂等 |

## 7. billing schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `billing.credit_accounts` | `id`、TENANT、`currency_code`、`granted_total`、`spent_total`、`active_hold_total`、`status`、`version`、AUDITABLE | workspace+currency 唯一；数值非负 |
| `billing.credit_holds` | `id`、TENANT、`account_id`、`business_type`、`business_id`、`requested_amount`、`settled_amount`、`released_amount`、`status`、`expires_at`、AUDITABLE | business 单据唯一；settled+released≤requested |
| `billing.credit_transactions` | `id`、TENANT、`account_id`、`transaction_no`、`type`、`amount bigint`、`direction`、`business_type`、`business_id`、`related_transaction_id`、`posted_at`、`metadata jsonb` | transaction_no 唯一；追加写；business 索引 |
| `billing.orders` | `id`、TENANT、`order_no`、`order_type`、`amount_minor`、`currency`、`status`、`payment_channel`、`paid_at`、AUDITABLE | order_no 唯一 |
| `billing.order_items` | `id`、`order_id`、`sku_code`、`quantity`、`unit_amount_minor`、`entitlement jsonb` | order_id 索引 |
| `billing.refunds` | `id`、TENANT、`refund_no`、`order_id`、`original_transaction_id`、`amount_minor`、`reason_code`、`status`、`approved_by`、AUDITABLE | refund_no 唯一；累计≤可退金额 |
| `billing.invoices` | `id`、TENANT、`invoice_no`、`order_ids uuid[]`、`title`、`tax_no_ciphertext`、`amount_minor`、`status`、`issued_at`、AUDITABLE | invoice_no 唯一；敏感字段加密 |
| `billing.reconciliation_records` | `id`、`channel`、`statement_date`、`external_no`、`internal_no`、`amount_minor`、`difference_minor`、`status`、`detail jsonb` | `(channel,statement_date,external_no)` 唯一 |

## 8. delivery schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `delivery.audio_clips` | `id`、TENANT、`project_id`、`episode_id`、`shot_id`、`clip_type`、`speaker_asset_id`、`text`、`object_key`、`duration_ms`、`version_no`、`status` | 对象与版本索引 |
| `delivery.subtitle_tracks` | `id`、TENANT、`project_id`、`episode_id`、`language`、`format`、`cues jsonb`、`version_no`、`status` | `(episode_id,language,version_no)` 唯一 |
| `delivery.lip_sync_versions` | `id`、TENANT、`shot_id`、`source_video_id`、`audio_clip_id`、`task_id`、`version_no`、`status`、`quality_result jsonb` | `(shot_id,version_no)` 唯一 |
| `delivery.timelines` | `id`、TENANT、`project_id`、`episode_id`、`version_no`、`frame_rate numeric(8,3)`、`duration_ms`、`status`、AUDITABLE | `(episode_id,version_no)` 唯一 |
| `delivery.timeline_tracks` | `id`、`timeline_id`、`track_type`、`track_no`、`name`、`muted`、`locked` | `(timeline_id,track_type,track_no)` 唯一 |
| `delivery.timeline_clips` | `id`、`track_id`、`source_type`、`source_id`、`start_ms`、`end_ms`、`source_in_ms`、`source_out_ms`、`config jsonb`、`sequence_no` | end>start；track+sequence 索引 |
| `delivery.final_videos` | `id`、TENANT、`project_id`、`episode_id`、`current_version_id`、AUDITABLE | project/episode 索引 |
| `delivery.final_video_versions` | `id`、TENANT、`final_video_id`、`version_no`、`timeline_id`、`render_task_id`、`object_key`、`sha256`、`duration_ms`、`status`、`created_at` | `(final_video_id,version_no)` 唯一 |
| `delivery.review_links` | `id`、TENANT、`project_id`、`final_version_id`、`token_hash`、`password_hash`、`permissions jsonb`、`watermark_config jsonb`、`expires_at`、`revoked_at`、AUDITABLE | token_hash 唯一；有效期索引 |
| `delivery.review_sessions` | `id`、`review_link_id`、`viewer_hash`、`ip`、`verified_at`、`expires_at`、`last_seen_at` | link+viewer 索引 |
| `delivery.review_comments` | `id`、`review_link_id`、`session_id`、`final_version_id`、`timecode_ms`、`content`、`severity`、`screenshot_object_key`、`status`、`created_at` | version/timecode 索引 |
| `delivery.approval_records` | `id`、TENANT、`project_id`、`final_version_id`、`review_link_id`、`decision`、`comment`、`signer_hash`、`decided_at` | version+decision 索引；追加写 |
| `delivery.compliance_checks` | `id`、TENANT、`project_id`、`project_snapshot_id`、`rule_set_version`、`input_digest`、`status`、`risk_summary jsonb`、`checked_at` | snapshot+rules 唯一 |
| `delivery.compliance_records` | `id`、TENANT、`check_id`、`rule_code`、`risk_level`、`target_type`、`target_id`、`evidence jsonb`、`status`、`owner_id`、`resolved_at` | check/risk/status 索引 |
| `delivery.rights_records` | `id`、TENANT、`project_id`、`target_type`、`target_id`、`rights_type`、`holder`、`license_scope jsonb`、`proof_object_key`、`status`、`valid_from`、`valid_to` | target/status/valid_to 索引 |
| `delivery.export_tasks` | `id`、TENANT、`project_id`、`project_snapshot_id`、`compliance_check_id`、`export_type`、`config jsonb`、`status`、`credit_hold_id`、`result_object_key`、`failure_code`、AUDITABLE | snapshot/type/status 索引 |
| `delivery.publish_packages` | `id`、TENANT、`export_task_id`、`target_platform`、`rule_version`、`manifest jsonb`、`object_key`、`sha256`、`created_at` | export+platform 唯一 |

## 9. market schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `market.templates` | `id`、TENANT、`owner_user_id`、`template_type`、`name`、`current_version_id`、`visibility`、`status`、AUDITABLE、SOFT_DELETE | owner/type/name 有效唯一 |
| `market.template_versions` | `id`、`template_id`、`version_no`、`manifest jsonb`、`license jsonb`、`price_minor`、`status`、`created_at` | `(template_id,version_no)` 唯一 |
| `market.template_reviews` | `id`、`template_version_id`、`reviewer_id`、`decision`、`issues jsonb`、`decided_at` | 追加写 |
| `market.market_items` | `id`、`item_type`、`source_id`、`seller_workspace_id`、`title`、`price_minor`、`license jsonb`、`status`、AUDITABLE | item_type/source 唯一 |
| `market.fork_records` | `id`、TENANT、`source_project_id`、`target_project_id`、`source_snapshot_id`、`license_snapshot jsonb`、`revenue_share jsonb`、`created_at` | target_project 唯一；血缘追加写 |
| `market.commercial_orders` | `id`、TENANT、`order_no`、`client_workspace_id`、`creator_workspace_id`、`title`、`requirements jsonb`、`amount_minor`、`status`、AUDITABLE | order_no 唯一 |
| `market.commercial_milestones` | `id`、`commercial_order_id`、`sequence_no`、`name`、`due_at`、`amount_minor`、`status` | order+sequence 唯一 |
| `market.commercial_deliveries` | `id`、`milestone_id`、`project_snapshot_id`、`review_link_id`、`submitted_at`、`status` | milestone/status 索引 |
| `market.acceptance_records` | `id`、`delivery_id`、`decision`、`comment`、`decided_by_hash`、`decided_at` | 追加写 |
| `market.settlements` | `id`、`commercial_order_id`、`settlement_no`、`gross_minor`、`platform_fee_minor`、`payable_minor`、`status`、`settled_at` | settlement_no 唯一；金额守恒 |

## 10. admin schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `admin.admin_staff` | `id`、`staff_no`、`name`、`credential_user_id`、`status`、`department`、`mfa_required`、AUDITABLE | staff_no、credential_user_id 唯一 |
| `admin.admin_roles` | `id`、`code`、`name`、`data_scope`、`risk_level`、AUDITABLE | code 唯一 |
| `admin.admin_permissions` | `id`、`code`、`resource`、`action`、`risk_level` | code 唯一 |
| `admin.admin_role_permissions` | `role_id`、`permission_id`、`approval_policy jsonb` | 复合主键 |
| `admin.admin_staff_roles` | `staff_id`、`role_id`、`valid_from`、`valid_to` | staff/role/valid_to 索引 |
| `admin.audit_logs` | `id`、`staff_id`、`request_id`、`action`、`target_type`、`target_id`、`workspace_id null`、`before_digest`、`after_digest`、`ip`、`result`、`occurred_at`、`detail_object_key` | request/action/target/time 索引；追加写 |
| `admin.approval_requests` | `id`、`request_type`、`target_type`、`target_id`、`requested_by`、`risk_summary`、`status`、`required_approvals`、`expires_at`、AUDITABLE | target+active status 索引 |
| `admin.approval_decisions` | `id`、`approval_request_id`、`staff_id`、`decision`、`comment`、`decided_at` | 每 staff/request 唯一 |
| `admin.support_tickets` | `id`、`ticket_no`、`workspace_id`、`user_id`、`type`、`priority`、`related_type`、`related_id`、`assignee_id`、`status`、AUDITABLE | ticket_no 唯一；assignee/status 索引 |
| `admin.ticket_messages` | `id`、`ticket_id`、`sender_type`、`sender_id`、`content`、`attachments jsonb`、`internal_only`、`created_at` | ticket/time 索引 |
| `admin.feature_flags` | `id`、`key`、`scope_type`、`scope_id`、`value jsonb`、`version`、`effective_at`、AUDITABLE | key/scope 有效唯一 |
| `admin.platform_rules` | `id`、`platform_code`、`rule_type`、`version_no`、`rule jsonb`、`status`、`effective_at`、AUDITABLE | platform/type/version 唯一 |
| `admin.message_templates` | `id`、`code`、`channel`、`locale`、`subject`、`body`、`variables jsonb`、`version_no`、`status` | code/channel/locale/version 唯一 |
| `admin.notifications` | `id`、`recipient_type`、`recipient_id`、`template_id`、`payload jsonb`、`status`、`scheduled_at`、`sent_at`、`failure_code` | recipient/status/time 索引 |
| `admin.alert_rules` | `id`、`code`、`metric`、`condition jsonb`、`severity`、`receivers jsonb`、`enabled`、AUDITABLE | code 唯一 |
| `admin.incidents` | `id`、`incident_no`、`source`、`severity`、`title`、`detail jsonb`、`owner_id`、`status`、`opened_at`、`resolved_at` | incident_no 唯一；status/severity 索引 |
| `admin.api_clients` | `id`、`client_no`、`owner_workspace_id`、`name`、`scopes text[]`、`status`、`rate_limit jsonb`、AUDITABLE | client_no 唯一 |
| `admin.api_keys` | `id`、`client_id`、`key_prefix`、`secret_hash`、`expires_at`、`revoked_at`、`last_used_at` | key_prefix 唯一；不存明文 |
| `admin.webhook_endpoints` | `id`、`client_id`、`url_ciphertext`、`event_types text[]`、`secret_ref`、`status`、AUDITABLE | client/status 索引 |
| `admin.api_usage_records` | `id`、`client_id`、`request_id`、`operation_id`、`status_code`、`latency_ms`、`occurred_at` | client/time/operation 索引；追加写 |

## 11. analytics schema

| 表 | 关键字段 | 约束与索引 |
|---|---|---|
| `analytics.events` | `id`、`workspace_id`、`user_id`、`event_name`、`object_type`、`object_id`、`properties jsonb`、`occurred_at`、`ingested_at` | event/time/workspace 索引；禁止放敏感原文 |
| `analytics.metric_snapshots` | `id`、`metric_key`、`scope_type`、`scope_id`、`period_start`、`period_end`、`dimensions jsonb`、`value numeric`、`calculated_at` | metric/scope/period/dimensions 唯一 |

## 12. 数据库级保护

- 所有租户查询通过仓储层强制带 `workspace_id`；生产数据库启用 PostgreSQL RLS 作为纵深防御。
- 账务、审计表拒绝 UPDATE/DELETE 的应用角色权限。
- 外键默认 `RESTRICT`；仅纯关联表允许受控级联删除。
- migration 在预发执行回放和回滚演练；大表新增非空字段采用分阶段迁移。
- JSONB 字段在应用层按 schema 版本校验；高频查询字段必须提升为独立列。
