[CmdletBinding()]
param(
    [string]$Output = (Join-Path $PSScriptRoot '..\docs\evidence\foundation-baseline.md'),
    [string]$GradleExecutable = (Join-Path $PSScriptRoot '..\gradlew.bat'),
    [string]$ExistingArcReelJunit
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Invoke-Check([string]$Name, [string]$Command, [scriptblock]$Action) {
    $code = 0
    $capturedLines = [System.Collections.Generic.List[string]]::new()
    try {
        & $Action 2>&1 | ForEach-Object { $capturedLines.Add($_.ToString()) }
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            $code = $LASTEXITCODE
        }
    } catch {
        $code = 1
        $capturedLines.Add(($_ | Out-String).Trim())
    }
    $captured = $capturedLines -join "`n"

    [pscustomobject]@{
        Name = $Name
        Command = $Command
        Code = $code
        Result = if ($code -eq 0) { 'PASS' } else { 'FAIL' }
        Summary = ($captured.Trim() -replace '\|', '\|' -replace "`r?`n", '<br>')
    }
}

function Assert-ExternalCommand([string]$Name) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Name 失败，退出码 $LASTEXITCODE"
    }
}

$results = @()
$results += Invoke-Check 'v9.2 规格' 'scripts/Test-ProductBaseline.ps1' {
    pwsh -NoProfile -File (Join-Path $PSScriptRoot 'Test-ProductBaseline.ps1')
    Assert-ExternalCommand 'v9.2 规格校验'
}
$results += Invoke-Check 'Java 测试' "$GradleExecutable test" {
    & $GradleExecutable test --console=plain
    Assert-ExternalCommand 'Java 测试'
}
$results += Invoke-Check 'Java 构建' "$GradleExecutable :platform-services:platform-app:bootJar" {
    & $GradleExecutable ':platform-services:platform-app:bootJar' --console=plain
    Assert-ExternalCommand 'Java 构建'
}
$arcReelCommand = if ($ExistingArcReelJunit) {
    "验证现有 Linux/Python 3.12 JUnit：$ExistingArcReelJunit"
} else {
    'scripts/Test-ArcReelBackendBaseline.ps1'
}
$results += Invoke-Check 'ArcReel 后端' $arcReelCommand {
    if ($ExistingArcReelJunit) {
        $junitPath = (Resolve-Path $ExistingArcReelJunit).Path
        [xml]$junit = Get-Content -LiteralPath $junitPath -Raw
        $suites = @($junit.testsuites.testsuite)
        if ($suites.Count -eq 0) {
            throw "JUnit 根节点不是 testsuites：$junitPath"
        }
        $tests = ($suites | Measure-Object -Property tests -Sum).Sum
        $failures = ($suites | Measure-Object -Property failures -Sum).Sum
        $errors = ($suites | Measure-Object -Property errors -Sum).Sum
        $skipped = ($suites | Measure-Object -Property skipped -Sum).Sum
        if ($tests -le 0 -or $failures -ne 0 -or $errors -ne 0) {
            throw "ArcReel JUnit 未通过：tests=$tests, failures=$failures, errors=$errors, skipped=$skipped"
        }
        Write-Output "ArcReel Linux/Python 3.12 JUnit: tests=$tests, failures=$failures, errors=$errors, skipped=$skipped"
    } else {
        pwsh -NoProfile -File (Join-Path $PSScriptRoot 'Test-ArcReelBackendBaseline.ps1')
        Assert-ExternalCommand 'ArcReel 后端测试'
    }
}
$results += Invoke-Check 'ArcReel 前端' 'corepack pnpm install --frozen-lockfile && lint && check && build' {
    Push-Location (Join-Path $repositoryRoot 'frontend')
    try {
        corepack pnpm install --frozen-lockfile
        Assert-ExternalCommand 'ArcReel 前端依赖安装'
        corepack pnpm lint
        Assert-ExternalCommand 'ArcReel 前端 lint'
        corepack pnpm check
        Assert-ExternalCommand 'ArcReel 前端 check'
        corepack pnpm build
        Assert-ExternalCommand 'ArcReel 前端 build'
    } finally {
        Pop-Location
    }
}
$results += Invoke-Check '本地基础设施' 'docker compose ps + container health' {
    Push-Location $repositoryRoot
    try {
        docker compose --env-file infra/compose/.env -f infra/compose/dev.compose.yml ps
        Assert-ExternalCommand 'Docker Compose 状态检查'
        $containers = @(
            'xingjing-dev-postgres-1',
            'xingjing-dev-rabbitmq-1',
            'xingjing-dev-minio-1'
        )
        foreach ($container in $containers) {
            $health = (docker inspect --format '{{.State.Health.Status}}' $container).Trim()
            Assert-ExternalCommand "$container 健康检查"
            Write-Output "$container=$health"
            if ($health -ne 'healthy') {
                throw "$container 状态为 $health"
            }
        }
    } finally {
        Pop-Location
    }
}

$jar = Get-ChildItem (Join-Path $repositoryRoot 'platform-services\platform-app\build\libs\*.jar') -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$jarEvidence = if ($jar) {
    "$($jar.Name): $((Get-FileHash -Algorithm SHA256 $jar.FullName).Hash.ToLowerInvariant())"
} else {
    '未生成 JAR；Java 构建结果为 FAIL'
}
$commit = (git -C $repositoryRoot rev-parse HEAD).Trim()
$verifiedAt = [DateTimeOffset]::UtcNow.ToString('O')
$blocking = @($results | Where-Object Result -eq 'FAIL')
$blockingText = if ($blocking.Count -gt 0) { ($blocking.Name -join '、') } else { '无' }

$lines = @(
    '# 平台地基验证证据',
    '',
    '## 基线',
    '- 产品基线：XJ-WEB-9.2',
    "- 代码提交：``$commit``",
    "- 验证时间：$verifiedAt",
    "- 构建产物：$jarEvidence",
    '',
    '## 验证结果',
    '| 检查 | 命令 | 结果 | 证据摘要 |',
    '|---|---|---|---|'
)
foreach ($result in $results) {
    $summary = if ($result.Summary.Length -gt 2000) {
        $result.Summary.Substring(0, 2000) + '…'
    } else {
        $result.Summary
    }
    $lines += "| $($result.Name) | ``$($result.Command)`` | $($result.Result) | $summary |"
}
$lines += @('', '## 阻断项', $blockingText)

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
Set-Content -LiteralPath $Output -Value $lines -Encoding utf8NoBOM

if ($blocking.Count -gt 0) {
    Write-Error "平台地基验证失败：$blockingText"
    exit 1
}
Write-Output "Foundation evidence written: $Output"
