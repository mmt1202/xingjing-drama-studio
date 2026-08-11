[CmdletBinding()]
param(
    [string]$BaselineRoot = (Join-Path $PSScriptRoot '..\docs\product\v9.2')
)

$ErrorActionPreference = 'Stop'
$required = @(
    'README.md',
    '00-governance\source-of-truth.md',
    '01-product\scope-and-priority.md',
    '02-pages\creator-pages.md',
    '02-pages\team-pages.md',
    '02-pages\admin-pages.md',
    '02-pages\client-pages.md',
    '06-api\openapi.yaml',
    '11-quality\traceability-matrix.md',
    'baseline.sha256'
)

$missing = $required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $BaselineRoot $_)) }
if ($missing) {
    throw "v9.2 基线缺少文件: $($missing -join ', ')"
}

$matrix = Get-Content -Raw -LiteralPath (Join-Path $BaselineRoot '11-quality\traceability-matrix.md')
$rows = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ ')
if ($rows.Count -ne 197) {
    throw "追踪矩阵应为 197 行，实际为 $($rows.Count)"
}

$stageCounts = @{
    P0 = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ .*\| P0 \|').Count
    P1 = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ .*\| P1 \|').Count
    P2 = [regex]::Matches($matrix, '(?m)^\| (CR|TM|AD|CL)-\d+ .*\| P2 \|').Count
}
if ($stageCounts.P0 -ne 56 -or $stageCounts.P1 -ne 121 -or $stageCounts.P2 -ne 20) {
    throw "阶段数量错误: P0=$($stageCounts.P0), P1=$($stageCounts.P1), P2=$($stageCounts.P2)"
}

$forbidden = Get-ChildItem -LiteralPath $BaselineRoot -Recurse -File |
    Where-Object Extension -in '.md', '.yaml', '.yml' |
    Select-String -Pattern '\bTBD\b|\bTODO\b|待补充'
if ($forbidden) {
    $paths = ($forbidden.Path | Sort-Object -Unique) -join ', '
    throw "基线包含占位内容: $paths"
}

$manifestPath = Join-Path $BaselineRoot 'baseline.sha256'
$manifestLines = Get-Content -LiteralPath $manifestPath | Where-Object { $_.Trim() }
foreach ($line in $manifestLines) {
    $hash, $relative = $line -split '  ', 2
    $target = Join-Path $BaselineRoot $relative
    if (-not (Test-Path -LiteralPath $target)) {
        throw "哈希清单文件不存在: $relative"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
    if ($actual -ne $hash) {
        throw "哈希不一致: $relative"
    }
}

Write-Output 'v9.2 baseline verified: 197 pages; P0=56, P1=121, P2=20'
