# 剧本流程根因与修复范围记录

日期：2026-10-24。证据 ID：EV-M03-SCRIPT-WORKFLOW-005。范围：小说原文到结构化剧本的转换、冻结前置、剧本子流程导航、导入成功状态和来源映射的诚实呈现。状态：本轮修复、专项、构建与正式浏览器／API／原文保留核验完成；本记录不代表 M03 或 A2 全部完成。

本记录可在实施完成后补充结果，不修改冻结的 `docs/product/v9.2`。本轮不扩展为分集来源统一、导演确认版本化、局部回滚或长篇全文分段编排。

## 应有流程与需求依据

1. 导入小说／剧本，保留原文与来源身份；导入成功和剧本结构合格是两个状态。
2. 将普通小说转为可编辑的结构化剧本候选，验证场景、段落及来源关系，再形成新的业务版本。
3. 用户从同一剧本上下文进入解析、改写、比较、来源查看；保存和切换不得混用另一个剧本或遗失错误状态。
4. 冻结明确的合格版本；新分析／改写请求应在付费调用前检查当前修订及可操作状态。
5. 映射只声明能够证明的来源关系；新增、改编或不能确认原文锚点的内容保留未映射，不以新文本坐标冒充原小说坐标。

冻结需求依据：CR-090 支持小说与分章节导入，CR-091 要求从原文形成结构化剧本，CR-093 要求改编与版本生成，CR-094 要求回到原小说章节，CR-095 包含局部回滚；AI 导演要求基于冻结剧本生成并确认方案。见 [创作者页合同](../product/v9.2/02-pages/creator-pages.md) 和 [M03 模块台账](../project/requirements/modules/M03-剧本导入、内容理解与%20AI%20导演.md)。

## 已确认根因

### 小说解析与结构化剧本混为一谈

诊断时 `parse_script` 只识别 `场景：...` 标题。普通章节小说导入后零场景，保存 `SCENE_HEADING_REQUIRED`。旧 AI 分析结果 schema 只有人物、地点、道具、关系、情节点和分集建议；`apply_analysis` 原样重解析当前正文，再替换 `analysis`，不会把小说转换成场景与段落。

因此即使 AI 返回完全合法的分析 JSON，普通小说依旧不能通过结构校验和冻结。这个问题是转换合同缺失，不是缺少模型配置。相关代码位于 `server/xingjing_content/parser.py`、`ai.py`、`service.py`。

只读观察：普通三行章节小说经原 parser 得到 `novelScenes=0`，校验码为 `SCENE_HEADING_REQUIRED`；没有为该观察调用模型或保存业务数据。

### 改写坐标被当作原小说映射

诊断时 `revise_script` 对新正文重新执行 parser，而 `SourceMapping` 仅保存目标 ID、`source_start` 和 `source_end`。偏移始终对应当前版本文本，不包含最初原文、章节或来源版本身份。

只读观察：原剧本长度 16，改写后的段落映射区间为 `[10,46]`，终点超过原文长度。该区间可正确切出新版对白，但不能定位原小说。前端原文映射页原先直接展示当前偏移 JSON，不能证明来源追溯。

本轮正确边界是识别并如实呈现未映射内容；不得为了显示完整映射而生成未经核验的坐标或置信度。完整的原文章节定位合同仍需后续设计。

### 冻结状态检查晚于 AI 调用

诊断时 HTTP `analyze`／`rewrite` 先等待 AI，再由 `apply_analysis`／`revise_script` 检查 `SCRIPT_FROZEN`。AI 层两种动作缺少对应冻结前置，只有导演生成检查了冻结状态。

有管理权限的客户端直接请求已冻结剧本时，可能先产生外部调用和结果记录，最终才拒绝保存；前端禁用按钮不能保护服务端入口。修复需在新请求认领和 Provider 调用前检查状态，同时保留已完成命令的稳定回执，不重复调用模型。

### 子流程与成功状态缺少明确上下文

原页面分散在导入、解析、原文、改写、映射、比较和导演入口；“文件导入成功”不能同时意味着结构合格、解析完成或可冻结。长篇原文、无场景结构和模型拒绝等状态应给出下一步，而非统一成功提示。切换子流程必须保留明确的 `selectedScriptId`，不隐式采用列表中另一个剧本。

前端及 bridge 已完成子流程与明确选中剧本的传输合同；源码已冻结，静态与既有 pages 回归通过。正式浏览器已核对同文档跨页、长度门禁、冻结恢复与草稿离开保护，结果见下文。

## 约 430k 长原文的受控边界

旧 AI 路径将 `current_version.source_content` 全文拼入 prompt，导入文件的 20 MiB 上限不能视为单次模型处理能力。`DEFAULT_MAX_OUTPUT_TOKENS=64000` 是输出预算，也不能证明模型可以接收约 43 万字符的输入。现有文本模型配置没有覆盖所有供应商的统一上下文窗口真相源，不能用固定字符/token 比宣称必然安全。

本轮最低要求：

- 原文可以按导入合同完整保存；AI 单次处理规模另行校验。
- 本轮应用单次上限为 20,000 字符，超限返回 HTTP 413、`CONTENT_AI_INPUT_TOO_LARGE`，details 含 `actualCharacters`、`maxCharacters: 20000`、`inputScope: current_script_version`。新命令的校验在认领和创建／调用文本生成器前完成，已完成缓存优先重放。改写指令另有 2000 字符上限，scope 为 `rewrite_instruction`，也不能误拦已有完成回执。
- 不能静默使用前 N 字、擅自摘要后称全文已分析，或丢弃后半段后保存为完整剧本。
- 用户必须看见恢复路径，例如按章节另行导入或明确选择片段；若没有交付分段编排，不得宣称已经支持长篇自动全量改编。
- 模型结构化输出截断应保留失败语义，不将截断结果或结构修复后的片段冒充完整产物。

这属于应用级处理策略，不是供应商 token 能力声明。本轮没有发送实际长原文、付费请求或外部服务调用。

主代理已只读核对既有文本 backend 边界：请求携带 `response_schema` 时，`lib/text_backends/base.py` 的 `check_truncation` 将已知输出截断升级为 `TextOutputTruncatedError`，并非仅日志 warning。本轮 AI 请求使用结构化 schema，复用这一既有边界；该源码核对不等于真实供应商截断场景已执行。

## 已确认的版本读取合同

后端与主代理确认：版本 `sourceContent` 保留当前全文；`scriptMappings` 表示当前剧本版本的坐标，不称为原文来源。仅当当前正文与不可变 `SourceDocument` 全文完全相等时，`sourceMappings` 才能表示可证明的原文区间，且 `sourceMappingCoordinateSystem=original_source`；其他情况返回空 `sourceMappings`、`sourceMappingCoordinateSystem=unmapped`，并提供 `unmappedParagraphIds`。这是一种保守且可解释的范围声明，不代表本轮已经实现了改编到原小说章节的语义对齐。

该诚实投影必须覆盖领域公开的 `ScriptDocument.downstream_snapshot()`，不能只修 HTTP 后仍在下游快照中把当前版偏移称为 `source_mappings`。最小一致做法是在领域集中指定版本的映射投影，HTTP 使用 camelCase、下游快照使用 snake_case；持久表的内部旧字段可继续承载当前版坐标，不据此扩大为来源对齐已完成。只读检查确认现有资产运行时读取冻结正文与摘要，智能分镜重新解析冻结正文，未发现这些运行入口直接消费该映射字段；完整语义对齐不在本轮范围。

前置 Markdown 标题以只读 `sourcePreamble` 元数据呈现；普通叙事文本不能因位于首场景之前而被静默丢弃。`动作`／`action` 标签作为动作段落，`旁白`／`narration` 作为旁白，支持标签与冒号之间的空格，不误识别为人物对白。上述合同已落盘并通过专项验证。

剧本详情新增 `/api/v1/projects/{projectId}/script-documents/{scriptId}` 别名，避免被主应用既有文件式 `/scripts/{name}` 路由抢占。新旧领域详情入口共享同一个可信权限与项目归属检查，不借别名绕过鉴权；正式主应用路由验证由主代理负责。

## 本轮验收清单与当前状态

| 边界 | 最终需要的证据 | 当前状态 |
|---|---|---|
| 小说到结构化剧本 | 普通小说产生合法场景／段落的新版本；原文不被覆盖；无效结构不入成功版本 | 实际领域／SQL／HTTP 通过；模型为验证端口，未声称真实模型输出验收 |
| 冻结前置 | 已冻结的新 analyze／rewrite 请求不创建生成器、不调用 Provider、不增加剧本版本；完成回执不被错误破坏 | 实际 SQL／HTTP 及独立只读回归通过 |
| 超长输入 | 约 430k 字符在调用前受控；原文完整保留；不以切片冒充全文 | 430,795 字符 HTTP 413、零 claim；另 430,000 字符独立回归通过 |
| 来源映射 | 改编新增段落显示未映射；不返回未经核验的原文区间 | DTO、比较、领域快照与统一版本 reader 同口径；专项通过 |
| 导入与子流程 | 导入保存、结构待修、AI 待处理／失败分别呈现；明确选中剧本跨页保留 | 源码、前端专项与正式浏览器通过：所选文档跨页保留，未保存内容拦截离开，明确放弃后恢复导航 |
| 权限及持久性 | 沿用可信项目范围、CAS、稳定幂等、审计，失败不覆盖原文或已保存版本 | 实际领域／SQL／HTTP 通过；正式用户原文核验另列 |
| 历史 AI 成功回执 | 旧分析仅含 metadata、没有 screenplay 时不假造转换，不二次调用；明确提示需要新操作转换 | 稳定 409 `CONTENT_AI_LEGACY_RESULT_REQUIRES_NEW_OPERATION`，SQL 整链及独立回归通过 |
| 正式用户项目保留 | 导入原文／当前版本的只读摘要与行数核对，确认本轮未改写既有用户输入 | 正式列表与详情200；六条记录及原文／各版本length和SHA-256与before全部一致；无项目写入或Provider调用 |

未创建、生成或修改测试文件、测试脚本、夹具或快照。代码静态检查、既有回归、直接 SQL／ASGI 断言、正式 API 或浏览器结果应分别记录，不能以其中一项替代其余验收。

主代理已完成的阶段性检查：

- 全量 basedpyright 记录 `.tmp/script-workflow-types.json` 为 **69 errors / 1079 warnings**，与既有基线相同；content、统一版本 reader 和 storyboard runtime 未出现类型错误，不将全库结果写成全绿。
- import-linter 为 **1 kept / 0 broken**。
- 正式浏览器已只读确认工作区导航恢复，原用户项目仍为六条未冻结剧本；该浏览器检查本身不是新转换流程终验，也不替代后文单独记录的原文 hash 保留核验。

主代理随后仅重新加载其管理的 FastAPI 后端，health ready，未重启 Docker、Java 身份或前端。正式 API 记录 `.tmp/script-workflow-live-api.json` 已由文档维护代理只读核对：`detailAliasStatus=200`、`listStatus=200`、`originalRecordsUnchanged=6`、`aiInputLimitCharacters=20000`、`providerCalls=0`、`writesToProjects=0`。主代理比较六条记录的 ID、revision、versionCount、locked，以及原文和每个版本的长度／SHA-256，均与 `.tmp/script-workflow-before.json` 一致；没有为验收修改原用户剧本或触发 AI 转换。此核验与隔离数据库中的构造输入验证分别记录。

前端代理已冻结源码，修改范围 ESLint、完整 TypeScript 检查及既有项目 pages 测试 **4/4 通过**。主代理全量 `pnpm check` 日志为 `.tmp/script-workflow-frontend-check.log`；文档维护代理已只读复核尾部与失败文件，结果 **1832 通过 / 25 失败**，168 个文件中 165 通过／3 失败。失败分布为既有 Onboarding 19、LoginPage 4、根路由 2，与批准基线一致，没有新增失败。

后端代理已冻结源码并提供最终结果：

- 隔离真实 SQLite、`ContentService`、`ContentAiService` 和 HTTP 整链 **45 项通过**。数据库为 `.tmp/script-workflow-direct-abf56uos/runtime.db`；文本生成器使用临时验证端口，六次调用均不访问外部模型。
- 45 项覆盖结构化新版本、原始 source 全文保留、430,795 字符超限零 claim、冻结前置零调用、完成缓存及 legacy 升级恢复、重复 freeze 不增加修订或审计、正文／映射／前置标题／动作标签、导演失效与 revised 审计。超过 50,000 字符的非法模型响应拒绝修复，未截前 N 字充当完整响应。
- 既有后端回归 **24 通过**：domain 10、persistence 9、HTTP 5。最终详情别名与比较六字段调整后重新运行 HTTP 5 通过，该重复运行不再加计一次。
- 后端九个修改源文件 Ruff／格式检查通过，修改范围 basedpyright **0 errors / 0 warnings**。

最终 `pnpm build` 通过（`.tmp/script-workflow-build.log`），包括完整 TypeScript 检查；新增工作流文件 ESLint 通过。分镜运行时既有用例另有 **5 通过**。后端既有用例使用模块 `--confcutdir` 和新建工作区 `--basetemp` 运行：根测试收集存在既有分类 marker 缺失，Windows 全局 pytest 临时目录另有权限问题；上述数字是隔离既有用例结果，不代表全量 pytest 已通过。

正式浏览器验证使用单独预览页，未保存任何业务写入、未调用模型：

- 指定 `scriptId` 的工作副本、解析、版本冻结、导演页跳转后仍指向同一文档，详情读入不再出现旧路由 404。
- 真实 430,795 字符文档展示 20,000 单次应用上限与手动选择本集／章节的恢复路径，解析按钮禁用，无自动截断。
- 版本页显示真实结构问题；明确标为“冻结最新工作版本 v1”的按钮禁用；导演页提供工作副本、解析、对比冻结的恢复入口。
- 只在独立预览页填写未保存的修订说明，点击项目总控被拦截；明确放弃后说明清空、保存按钮禁用，并可导航。该验证没有发保存请求。
- 只读截图 `output/script-workflow/director-recovery.png` 保留导航与前置说明；本地截图及临时证据不提交。

这些结果不替代真实供应商的创作质量或全流程生产联合验收。

独立只读收尾通过 **14 项 stdin 断言**，不写文件、不写 SQL、不创建生成器或调用 Provider：

- 430,000 字符输入返回完整的 20,000 上限与当前版本 scope，认领次数与模型创建次数均为 0。
- 已冻结的全新 analyze／rewrite 请求在调用前拒绝；新 schema 的已完成缓存可在后续锁定和修订变化后恢复。
- 旧 metadata-only 成功缓存返回明确 legacy 恢复状态；已完成 rewrite 的旧 3000 字符指令也不会被新增 2000 字符门槛误拦，未二次调用模型。
- 动作／旁白标签支持分隔空格；场景前普通叙事不能静默消失，Markdown 标题作为 metadata 保留。
- 新增段落在领域投影和冻结下游快照中均不冒称原文坐标；AI 情节点引用不存在的输出场景时拒绝。

另只读复核统一版本 reader：`ScriptRow.source_content` 持久化的是不可变导入原文，而不是当前版正文；以它和指定版本全文严格等值决定来源映射是正确口径。compareVersions 也已改为同一领域映射投影。内容编辑、结构分析和整版恢复清除旧导演内容，新增内容版本的审计动作优先于伴随的导演失效，避免将内容修订仅记录为导演更新；这不等于已交付导演确认版本化。

## 明确保留的 P1／P2 工作

| 优先级 | 未完成事项 | 具体断链与边界 |
|---|---|---|
| P1 | 分集来源统一与各集剧本交接 | M03 文件导入保存 SQL 剧本；分集从项目 `source/` 文件读取，后续导入／修订未统一来源身份。按段落分集仅产生范围与文件，不自动形成各集 SQL 剧本版本；生产绑定禁止两集复用同一 `scriptVersionId`，分镜又要求各集明确剧本与资产快照。因此不能把“分出了文字区间”当作“各集可生产” |
| P1 | 导演候选、确认与版本引用 | 当前生成后直接覆盖可变 `director_profile`；阶段以任意字段有值判断完成，智能分镜读取当前 profile。尚无绑定冻结剧本版本的独立确认记录和下游精确引用，不能称导演确认闭环已完成 |
| P2 | 剧本局部回滚 | 统一版本已提供整版恢复，尚无段落／字段范围的局部合并与来源保留合同；整版恢复不等同于 CR-095 的局部回滚 |

上述范围由 `server/xingjing_projects_runtime/episode_production.py`、`lib/episode_editor.py`、`server/xingjing_projects_runtime/control.py`、`server/xingjing_content/service.py` 和 `server/xingjing_storyboard_runtime/runtime.py` 的现有分支确认。本轮不修改这些后续合同，也不以当前修复宣称剧本、分集、导演与下游生产整体全部完成。
