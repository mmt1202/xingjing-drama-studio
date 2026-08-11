[CmdletBinding()]
param(
    [string]$RequirementsRoot = (Join-Path $PSScriptRoot '..\docs\project\requirements'),
    [string]$MatrixPath = (Join-Path $PSScriptRoot '..\docs\product\v9.2\11-quality\traceability-matrix.md')
)

$ErrorActionPreference = 'Stop'

$requiredFiles = @(
    '00-使用与状态规则.md',
    '01-需求总览与完成度.md',
    '02-逐页需求查询索引.md',
    '03-平台共享能力台账.md',
    '04-验收与测试证据.md',
    '05-变更与缺口清单.md',
    '06-依赖关系与联合验收.md'
)
$missing = $requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RequirementsRoot $_)) }
if ($missing) { throw "需求台账缺少总控文件: $($missing -join ', ')" }

$moduleRoot = Join-Path $RequirementsRoot 'modules'
$sharedRoot = Join-Path $RequirementsRoot 'shared'
$moduleFiles = @(Get-ChildItem -LiteralPath $moduleRoot -Filter 'M??-*.md' -File)
$sharedFiles = @(Get-ChildItem -LiteralPath $sharedRoot -Filter 'S??-*.md' -File)
if ($moduleFiles.Count -ne 19) { throw "功能模块文档应为 19 份，实际为 $($moduleFiles.Count)" }
if ($sharedFiles.Count -ne 7) { throw "共享能力文档应为 7 份，实际为 $($sharedFiles.Count)" }
if (Test-Path -LiteralPath (Join-Path $RequirementsRoot 'pages')) { throw '不应存在按页面拆分的 pages 目录' }
$appendixFiles = @(Get-ChildItem -LiteralPath (Join-Path $RequirementsRoot 'appendix\pages') -Recurse -Filter '*.md' -File)
if ($appendixFiles.Count -ne 197) { throw "逐页查询附录应为 197 份，实际为 $($appendixFiles.Count)" }

function ConvertTo-Cells([string]$line) {
    $parts = $line -split '\|'
    return @($parts[1..($parts.Count - 2)] | ForEach-Object { $_.Trim() })
}

$allowedStatuses = @('未开始','开发中','待验收','已验证','阻断')
$ledgerRows = @()
foreach ($file in $moduleFiles) {
    foreach ($line in Get-Content -LiteralPath $file.FullName) {
        if ($line -notmatch '^\| (CR|TM|AD|CL)-\d+ \|') { continue }
        $cells = ConvertTo-Cells $line
        if ($cells.Count -ne 6 -or $cells[3] -notin $allowedStatuses) { continue }
        $ledgerRows += [pscustomobject]@{ Id=$cells[0]; Title=$cells[1]; Stage=$cells[2]; Status=$cells[3]; Evidence=$cells[4]; Gap=$cells[5]; File=$file.Name }
    }
}
if ($ledgerRows.Count -ne 197) { throw "模块需求覆盖应为 197 项，实际为 $($ledgerRows.Count)" }

$duplicates = @($ledgerRows | Group-Object Id | Where-Object Count -ne 1)
if ($duplicates) { throw "需求 ID 重复: $($duplicates.Name -join ', ')" }

$matrixIds = @(Get-Content -LiteralPath $MatrixPath | Where-Object { $_ -match '^\| (CR|TM|AD|CL)-\d+ ' } | ForEach-Object { (ConvertTo-Cells $_)[0] })
$ledgerIds = @($ledgerRows.Id)
$notInLedger = @($matrixIds | Where-Object { $_ -notin $ledgerIds })
$notInMatrix = @($ledgerIds | Where-Object { $_ -notin $matrixIds })
if ($notInLedger) { throw "追踪矩阵需求未进入模块文档: $($notInLedger -join ', ')" }
if ($notInMatrix) { throw "模块文档存在未知需求 ID: $($notInMatrix -join ', ')" }

$expectedStages = @{ P0=56; P1=121; P2=20 }
foreach ($stage in $expectedStages.Keys) {
    $count = @($ledgerRows | Where-Object Stage -eq $stage).Count
    if ($count -ne $expectedStages[$stage]) { throw "$stage 应为 $($expectedStages[$stage]) 项，实际为 $count" }
}

$verifiedWithoutEvidence = @($ledgerRows | Where-Object { $_.Status -eq '已验证' -and ($_.Evidence -eq '—' -or [string]::IsNullOrWhiteSpace($_.Evidence)) })
if ($verifiedWithoutEvidence) { throw "已验证需求缺少证据: $($verifiedWithoutEvidence.Id -join ', ')" }

$sharedStatuses = @()
foreach ($file in $sharedFiles) {
    foreach ($line in Get-Content -LiteralPath $file.FullName) {
        if ($line -notmatch '^\| [A-Z]+-\d+ \|') { continue }
        $cells = ConvertTo-Cells $line
        if ($cells.Count -ne 5) { continue }
        if ($cells[3] -notin $allowedStatuses) { throw "共享能力状态非法: $($cells[0])=$($cells[3])" }
        if ($cells[3] -eq '已验证' -and ($cells[4] -eq '—' -or [string]::IsNullOrWhiteSpace($cells[4]))) { throw "已验证共享能力缺少证据: $($cells[0])" }
        $sharedStatuses += $cells[0]
    }
}
if ($sharedStatuses.Count -lt 70) { throw "共享能力拆分不足，至少应有 70 项，实际为 $($sharedStatuses.Count)" }

$acceptance = Get-Content -Raw -LiteralPath (Join-Path $RequirementsRoot '06-依赖关系与联合验收.md')
$acceptancePacks = @('A0','A1','A2','A3','A4','A5','A6','A7','A8','B1','B2','B3','C1','C2','C3','D1','D2','D3','D4')
$missingPacks = @($acceptancePacks | Where-Object { $acceptance -notmatch "\b$_\b" })
if ($missingPacks) { throw "联合验收包缺失: $($missingPacks -join ', ')" }

$forbidden = Get-ChildItem -LiteralPath $RequirementsRoot -Recurse -File -Filter '*.md' |
    Select-String -Pattern '\bTBD\b|\bTODO\b|待补充'
if ($forbidden) { throw "需求文档存在占位内容: $((($forbidden.Path | Sort-Object -Unique) -join ', '))" }

Write-Output "requirements ledger verified: modules=19; pages=197; appendices=197; shared=$($sharedStatuses.Count); P0=56; P1=121; P2=20; acceptance=19"
