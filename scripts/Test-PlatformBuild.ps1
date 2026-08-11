[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Join-Path $PSScriptRoot '..'),
    [string]$GradleExecutable = (Join-Path $PSScriptRoot '..\gradlew.bat')
)

$ErrorActionPreference = 'Stop'
$required = @(
    'settings.gradle.kts',
    'build.gradle.kts',
    'gradle\libs.versions.toml',
    'gradle\wrapper\gradle-wrapper.properties',
    'gradlew.bat',
    'platform-services\platform-core\build.gradle.kts',
    'platform-services\platform-app\build.gradle.kts'
)
$missing = $required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RepositoryRoot $_)) }
if ($missing) {
    throw "平台构建骨架缺少文件: $($missing -join ', ')"
}

Push-Location $RepositoryRoot
try {
    & $GradleExecutable projects --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Gradle projects 失败，退出码 $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

Write-Output 'platform build verified: Gradle projects loaded'
