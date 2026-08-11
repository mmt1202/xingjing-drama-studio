[CmdletBinding()]
param(
    [string]$ProductRoot = (Join-Path $PSScriptRoot '..\docs\product\v9.2'),
    [string]$RequirementsRoot = (Join-Path $PSScriptRoot '..\docs\project\requirements')
)

$ErrorActionPreference = 'Stop'
$matrixPath = Join-Path $ProductRoot '11-quality\traceability-matrix.md'
$moduleRoot = Join-Path $RequirementsRoot 'modules'
New-Item -ItemType Directory -Force -Path $moduleRoot | Out-Null

$modules = @(
    [pscustomobject]@{ Id='M01'; Title='账号、工作区与协作'; Domains=@('账号与访问','工作区与协作'); Depends='平台共享地基'; Enables='项目创建、团队协作、所有租户业务'; Bundle='A0 身份与租户地基闭环' },
    [pscustomobject]@{ Id='M02'; Title='项目、剧集与生产数据'; Domains=@('项目与剧集','数据分析'); Depends='M01、平台共享地基'; Enables='剧本、资产、生成、成本与交付'; Bundle='A1 项目生产容器闭环' },
    [pscustomobject]@{ Id='M03'; Title='剧本导入、内容理解与 AI 导演'; Domains=@('剧本与导演'); Depends='M01、M02、模型与任务能力'; Enables='主体资产提取、分镜规划'; Bundle='A2 内容理解与导演闭环' },
    [pscustomobject]@{ Id='M04'; Title='主体与素材资产'; Domains=@('主体与素材资产'); Depends='M02、M03、对象存储、版本能力'; Enables='分镜引用、图片与视频生成'; Bundle='A3 资产准备闭环' },
    [pscustomobject]@{ Id='M05'; Title='分镜与故事板'; Domains=@('分镜与故事板'); Depends='M03、M04、任务与模型能力'; Enables='图片、视频、音频生产'; Bundle='A4 分镜故事板闭环' },
    [pscustomobject]@{ Id='M06'; Title='图片、视频生成与模型调用'; Domains=@('生成与模型'); Depends='M04、M05、任务队列、模型路由、算力冻结'; Enables='成片剪辑、成本结算'; Bundle='A5 AI 媒体生成闭环' },
    [pscustomobject]@{ Id='M07'; Title='音频、配音、字幕与对口型'; Domains=@('音频字幕与对口型'); Depends='M03、M05、M06、对象存储'; Enables='成片合成与交付'; Bundle='A6 音频字幕闭环' },
    [pscustomobject]@{ Id='M08'; Title='成片剪辑、预览与版本'; Domains=@('成片剪辑'); Depends='M06、M07、版本与媒体处理'; Enables='审片、合规与正式导出'; Bundle='A7 成片闭环' },
    [pscustomobject]@{ Id='M09'; Title='合规、版权、导出与发布'; Domains=@('合规与版权','导出与发布'); Depends='M08、审核合规、算力与对象存储'; Enables='正式交付、发布包和外部发布'; Bundle='A8 合规交付闭环' },
    [pscustomobject]@{ Id='M10'; Title='团队、成员、权限与企业配置'; Domains=@('团队成员与权限','团队总览','团队审计','企业配置'); Depends='M01、M02、审计与 RBAC'; Enables='规模化协作、团队计费和客户交付'; Bundle='B1 团队治理闭环' },
    [pscustomobject]@{ Id='M11'; Title='团队计费、成本与权益'; Domains=@('团队计费与成本'); Depends='M10、算力账务、订单与发票'; Enables='团队成本控制和财务对账'; Bundle='B2 团队财务闭环' },
    [pscustomobject]@{ Id='M12'; Title='审片协作与客户交付'; Domains=@('客户审片','审片协作','团队客户审片'); Depends='M08、M09、M10、外链安全'; Enables='客户批注、审批和交付确认'; Bundle='B3 客户审片交付闭环' },
    [pscustomobject]@{ Id='M13'; Title='模板、社区、市场与 Fork'; Domains=@('模板','平台模板','生态与市场'); Depends='M04、M05、M09、授权与谱系'; Enables='内容复用、交易和二创生态'; Bundle='C1 模板市场与二创闭环' },
    [pscustomobject]@{ Id='M14'; Title='商单、验收与结算'; Domains=@('商单','平台商单'); Depends='M10、M11、M12、M09'; Enables='商业任务发布、交付、验收和结算'; Bundle='C2 商单履约闭环' },
    [pscustomobject]@{ Id='M15'; Title='平台总览与业务管理'; Domains=@('平台总览','平台业务管理'); Depends='平台共享地基、审计、各业务服务'; Enables='用户、项目、任务和内容治理'; Bundle='D1 平台业务治理闭环' },
    [pscustomobject]@{ Id='M16'; Title='平台模型、算力、财务与权益'; Domains=@('平台模型运营','平台财务与权益','算力与成本'); Depends='任务、模型供应商、账务与审计'; Enables='生成路由、计费、订单和财务运营'; Bundle='D2 模型算力财务闭环' },
    [pscustomobject]@{ Id='M17'; Title='平台审核、合规与安全治理'; Domains=@('平台审核合规','平台安全治理'); Depends='M09、M15、审计与安全基础'; Enables='内容放行、申诉、风控和高危操作治理'; Bundle='D3 审核合规安全闭环' },
    [pscustomobject]@{ Id='M18'; Title='平台运维、客服、消息与运营配置'; Domains=@('平台运维','平台客服','平台消息','平台运营配置','平台设置'); Depends='平台共享地基、监控、通知与审计'; Enables='稳定运营、客服处置、消息触达和策略配置'; Bundle='D4 平台运营保障闭环' },
    [pscustomobject]@{ Id='M19'; Title='开放平台与外部集成'; Domains=@('开放平台'); Depends='身份、权限、版本化 API、限流与审计'; Enables='第三方接入和企业私有化'; Bundle='C3 开放平台闭环' }
)

function ConvertTo-Cells([string]$line) {
    $parts = $line -split '\|'
    return @($parts[1..($parts.Count - 2)] | ForEach-Object { $_.Trim() })
}

$previous = @{}
Get-ChildItem -LiteralPath $RequirementsRoot -Recurse -Filter '*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
    foreach ($line in Get-Content -LiteralPath $_.FullName) {
        if ($line -notmatch '^\| (CR|TM|AD|CL)-\d+ \|') { continue }
        $cells = ConvertTo-Cells $line
        if ($cells.Count -ge 6 -and $cells[3] -in @('未开始','开发中','待验收','已验证','阻断')) {
            $previous[$cells[0]] = @{ Status=$cells[3]; Evidence=$cells[4]; Gap=$cells[5] }
        }
        elseif ($cells.Count -ge 16 -and $cells[10] -in @('未开始','开发中','待验收','已验证','阻断')) {
            $evidence = @($cells[11],$cells[12],$cells[13]) | Where-Object { $_ -and $_ -ne '—' }
            $previous[$cells[0]] = @{ Status=$cells[10]; Evidence=if($evidence){$evidence -join '<br>'}else{'—'}; Gap=$cells[14] }
        }
    }
}

$sections = @{}
$titles = @{}
$specByPrefix = @{ CR='creator-pages.md'; TM='team-pages.md'; AD='admin-pages.md'; CL='client-pages.md' }
foreach ($entry in $specByPrefix.GetEnumerator()) {
    $currentId = $null
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in Get-Content -LiteralPath (Join-Path $ProductRoot "02-pages\$($entry.Value)")) {
        if ($line -match '^##\s+((?:CR|TM|AD|CL)-\d+)\s+(.+?)\s*$') {
            if ($currentId) { $sections[$currentId] = @($lines) }
            $currentId = $Matches[1]
            $titles[$currentId] = $Matches[2]
            $lines = [System.Collections.Generic.List[string]]::new()
            continue
        }
        if ($currentId) { $lines.Add($line) }
    }
    if ($currentId) { $sections[$currentId] = @($lines) }
}

$rows = foreach ($line in Get-Content -LiteralPath $matrixPath) {
    if ($line -notmatch '^\| (CR|TM|AD|CL)-\d+ \|') { continue }
    $c = ConvertTo-Cells $line
    $old = $previous[$c[0]]
    [pscustomobject]@{
        Id=$c[0]; System=$c[1]; Route=$c[2]; Stage=$c[3]; Domain=$c[4]; Objects=$c[5]; Permission=$c[6]; Api=$c[7]; Tests=$c[8]
        Title=$titles[$c[0]]; Status=if($old){$old.Status}else{'未开始'}; Evidence=if($old){$old.Evidence}else{'—'}; Gap=if($old){$old.Gap}else{'—'}
    }
}
if ($rows.Count -ne 197) { throw "页面需求应为 197，实际为 $($rows.Count)" }

$assigned = @{}
foreach ($module in $modules) {
    foreach ($domain in $module.Domains) {
        if ($assigned.ContainsKey($domain)) { throw "业务域重复归属: $domain" }
        $assigned[$domain] = $module.Id
    }
}
$allDomains = @($rows.Domain | Sort-Object -Unique)
$missingDomains = @($allDomains | Where-Object { -not $assigned.ContainsKey($_) })
if ($missingDomains) { throw "存在未归档业务域: $($missingDomains -join ', ')" }

$overview = [System.Collections.Generic.List[string]]::new()
$overview.Add('# 星镜剧创功能模块需求总览与完成度')
$overview.Add('')
$overview.Add('> 需求按功能模块拆分，不按页面拆文档。页面 ID 只用于证明范围完整；最终验收按依赖闭环整组执行。')
$overview.Add('')
$overview.Add('| 模块 | 需求文档 | P0 | P1 | P2 | 合计 | 当前状态 | 联合验收包 |')
$overview.Add('|---|---|---:|---:|---:|---:|---|---|')
$moduleFileByRequirement = @{}

foreach ($module in $modules) {
    $moduleRows = @($rows | Where-Object { $_.Domain -in $module.Domains } | Sort-Object Stage,Id)
    $fileName = "$($module.Id)-$($module.Title).md"
    $p0 = @($moduleRows | Where-Object Stage -eq 'P0').Count
    $p1 = @($moduleRows | Where-Object Stage -eq 'P1').Count
    $p2 = @($moduleRows | Where-Object Stage -eq 'P2').Count
    $verified = @($moduleRows | Where-Object Status -eq '已验证').Count
    $moduleStatus = if ($verified -eq $moduleRows.Count -and $moduleRows.Count -gt 0) {'已验证'} elseif (@($moduleRows | Where-Object Status -eq '阻断').Count -gt 0) {'阻断'} elseif (@($moduleRows | Where-Object Status -in @('开发中','待验收','已验证')).Count -gt 0) {'开发中'} else {'未开始'}
    $overview.Add("| $($module.Id) $($module.Title) | [查看需求](modules/$fileName) | $p0 | $p1 | $p2 | $($moduleRows.Count) | $moduleStatus（$verified/$($moduleRows.Count) 已验证） | $($module.Bundle) |")

    $doc = [System.Collections.Generic.List[string]]::new()
    $doc.Add("# $($module.Id) $($module.Title)需求设计")
    $doc.Add('')
    $doc.Add("- 范围：$($module.Domains -join '、')")
    $doc.Add("- 页面覆盖：$($moduleRows.Count) 项（P0=$p0，P1=$p1，P2=$p2）")
    $doc.Add("- 完成度：$verified/$($moduleRows.Count) 已验证")
    $doc.Add("- 当前状态：$moduleStatus")
    $doc.Add("- 联合验收包：$($module.Bundle)")
    $doc.Add('')
    $doc.Add('## 1. 模块目标与边界')
    $doc.Add('')
    $doc.Add("本模块统一承载「$($module.Title)」相关需求。模块内页面、接口、数据、权限、异常与验收必须作为同一能力集合维护，不允许只完成可见页面而遗漏服务端规则或失败路径。")
    $doc.Add('')
    $doc.Add("- 前置依赖：$($module.Depends)")
    $doc.Add("- 下游能力：$($module.Enables)")
    $doc.Add('- 页面只做范围映射；模块完成必须通过联合验收，不以单页可打开为完成。')
    $doc.Add('')
    $doc.Add('## 2. 功能范围与完成状态')
    $doc.Add('')
    $doc.Add('| 需求ID | 功能/页面 | 阶段 | 状态 | 实现与验收证据 | 已知缺口 |')
    $doc.Add('|---|---|---|---|---|---|')
    foreach ($row in $moduleRows) {
        $moduleFileByRequirement[$row.Id] = $fileName
        $doc.Add("| $($row.Id) | $($row.Title) | $($row.Stage) | $($row.Status) | $($row.Evidence) | $($row.Gap) |")
    }
    $doc.Add('')
    $doc.Add('## 3. 数据、权限、接口与测试覆盖')
    $doc.Add('')
    $doc.Add('| 需求ID | 主要对象 | 权限 | API | 测试 |')
    $doc.Add('|---|---|---|---|---|')
    foreach ($row in $moduleRows) {
        $doc.Add("| $($row.Id) | $($row.Objects) | $($row.Permission) | $($row.Api) | $($row.Tests) |")
    }
    $doc.Add('')
    $doc.Add('## 4. 联合验收要求')
    $doc.Add('')
    $doc.Add("本模块并入「$($module.Bundle)」验收，不单独以页面数量结项。验收至少覆盖：")
    $doc.Add('')
    $doc.Add('1. 前置依赖真实可用，权限和租户上下文贯穿全流程；')
    $doc.Add('2. 模块内数据对象、状态机、API 和页面展示一致；')
    $doc.Add('3. 正常、空、失败、冲突、取消、超时、重试和恢复路径按适用范围通过；')
    $doc.Add('4. 模块产生的任务、费用、审计、合规和版本证据可以追踪；')
    $doc.Add('5. 下游模块能够消费本模块产物，刷新或重复请求不产生重复副作用。')
    $doc.Add('')
    $doc.Add('## 5. 详细功能需求')
    $doc.Add('')
    foreach ($row in $moduleRows) {
        $doc.Add("### $($row.Id) $($row.Title)")
        $doc.Add('')
        $doc.Add("- 系统：$($row.System)")
        $doc.Add("- 路由：$($row.Route)")
        $doc.Add("- 阶段：$($row.Stage)")
        $doc.Add("- 当前状态：$($row.Status)")
        $doc.Add('')
        foreach ($sectionLine in $sections[$row.Id]) { $doc.Add($sectionLine) }
    }
    $doc.Add('## 6. 本模块不单独判定完成的事项')
    $doc.Add('')
    $doc.Add('- 只有静态页面、原型或 mock 数据；')
    $doc.Add('- 只有接口或数据库，没有可用交互和错误恢复；')
    $doc.Add('- 只有单页测试，没有前置依赖与下游消费验证；')
    $doc.Add('- 缺少实现、自动化测试、运行证据或适用发布门禁。')

    Set-Content -LiteralPath (Join-Path $moduleRoot $fileName) -Value $doc -Encoding utf8NoBOM
}

$appendixRoot = Join-Path $RequirementsRoot 'appendix\pages'
$systemFolders = @{
    '创作者' = 'creator'
    '团队' = 'team'
    '平台后台' = 'admin'
    '客户审片' = 'client'
}
foreach ($folder in $systemFolders.Values) {
    New-Item -ItemType Directory -Force -Path (Join-Path $appendixRoot $folder) | Out-Null
}
foreach ($row in $rows) {
    $folder = $systemFolders[$row.System]
    if (-not $folder) { throw "未知系统，无法生成逐页附录: $($row.System)" }
    $safeTitle = $row.Title -replace '[\\/:*?"<>|]', '-'
    $pagePath = Join-Path (Join-Path $appendixRoot $folder) "$($row.Id)-$safeTitle.md"
    $moduleFile = $moduleFileByRequirement[$row.Id]
    $page = [System.Collections.Generic.List[string]]::new()
    $page.Add("# $($row.Id) $($row.Title)")
    $page.Add('')
    $page.Add('> 文档定位：逐页查询附录。一级需求范围、状态汇总和最终验收以所属功能模块文档为准。')
    $page.Add('')
    $page.Add('| 项目 | 内容 |')
    $page.Add('|---|---|')
    $page.Add("| 需求ID | $($row.Id) |")
    $page.Add("| 所属模块 | [查看模块需求](../../../modules/$moduleFile) |")
    $page.Add("| 所属端 | $($row.System) |")
    $page.Add("| 阶段 | $($row.Stage) |")
    $page.Add("| 业务域 | $($row.Domain) |")
    $page.Add("| 路由 | $($row.Route) |")
    $page.Add("| 当前状态 | $($row.Status) |")
    $page.Add("| 实现与验收证据 | $($row.Evidence) |")
    $page.Add("| 已知缺口 | $($row.Gap) |")
    $page.Add('')
    $page.Add('## 完整页面需求')
    $page.Add('')
    foreach ($sectionLine in $sections[$row.Id]) { $page.Add($sectionLine) }
    Set-Content -LiteralPath $pagePath -Value $page -Encoding utf8NoBOM
}

$appendixIndex = @(
    '# 星镜剧创逐页需求查询附录',
    '',
    '> 本目录用于按页面快速查询完整需求，不作为独立模块验收入口。主需求见 `../modules/`，整块验收见 `../06-依赖关系与联合验收.md`。',
    '',
    '| 端 | 数量 | 目录 |',
    '|---|---:|---|',
    '| 创作者端 | 124 | [creator](appendix/pages/creator) |',
    '| 团队/企业后台 | 18 | [team](appendix/pages/team) |',
    '| 平台运营后台 | 50 | [admin](appendix/pages/admin) |',
    '| 客户审片端 | 5 | [client](appendix/pages/client) |',
    '| **合计** | **197** | — |'
)
Set-Content -LiteralPath (Join-Path $RequirementsRoot '02-逐页需求查询索引.md') -Value $appendixIndex -Encoding utf8NoBOM

$overview.Add('| **平台共享地基** | [查看需求](03-平台共享能力台账.md) | — | — | — | 按能力条目 | 开发中 | A0-A8、B1-B3、C1-C3、D1-D4 的共同前置 |')
$overview.Add('')
$overview.Add('## 联合验收入口')
$overview.Add('')
$overview.Add('所有模块的依赖顺序、整组验收范围和阶段门禁统一见 [06-依赖关系与联合验收.md](06-依赖关系与联合验收.md)。')
$overview.Add('')
$overview.Add('## 状态维护规则')
$overview.Add('')
$overview.Add('- 子功能状态在对应模块文档中更新；本总览由模块状态汇总。')
$overview.Add('- 模块只有在对应联合验收包通过后才能标记「已验证」。')
$overview.Add('- 页面数量只证明 197 项需求均有归属，不作为完成率的唯一依据。')
Set-Content -LiteralPath (Join-Path $RequirementsRoot '01-需求总览与完成度.md') -Value $overview -Encoding utf8NoBOM

Write-Output "module requirements generated: modules=$($modules.Count); pages=$($rows.Count); appendices=$($rows.Count); domains=$($allDomains.Count)"
