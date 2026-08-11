[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Join-Path $PSScriptRoot '..'),
    [string]$JunitXml = (Join-Path $PSScriptRoot '..\.xingjing-data\arcreel-backend-pytest.xml'),
    [string]$DockerImage = 'ghcr.io/astral-sh/uv:python3.12-bookworm-slim'
)

$ErrorActionPreference = 'Stop'
$repositoryRootResolved = (Resolve-Path $RepositoryRoot).Path
$junitParent = Split-Path -Parent $JunitXml
New-Item -ItemType Directory -Force -Path $junitParent | Out-Null
Remove-Item -LiteralPath $JunitXml -Force -ErrorAction SilentlyContinue

if ($IsWindows) {
    docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'ArcReel Linux 基线需要可用的 Docker 服务'
    }
    docker volume create xingjing-uv-cache | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw '创建 uv Docker 缓存卷失败'
    }

    $containerName = "xingjing-arcreel-baseline-$PID"
    $mountRoot = $repositoryRootResolved.Replace('\', '/')
    $containerJunit = '/workspace/.xingjing-data/arcreel-backend-pytest.xml'
    $command = @(
        "sed -i 's@http://deb.debian.org@https://mirrors.cloud.tencent.com@g' /etc/apt/sources.list.d/debian.sources",
        'apt-get update -qq',
        'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg mediainfo bubblewrap socat',
        'uv sync --frozen',
        "uv run python -m pytest -m 'not e2e' -q --junitxml=$containerJunit"
    ) -join ' && '

    docker run --rm --name $containerName `
        --mount "type=bind,source=$mountRoot,target=/workspace" `
        --mount 'type=volume,source=xingjing-uv-cache,target=/root/.cache/uv' `
        --workdir /workspace `
        --env UV_PROJECT_ENVIRONMENT=/opt/venv `
        $DockerImage sh -lc $command
    $code = $LASTEXITCODE
} else {
    Push-Location $repositoryRootResolved
    try {
        uv sync --frozen --dev
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
        uv run --frozen python -m pytest -m 'not e2e' -q --junitxml=$JunitXml
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

if ($code -ne 0) {
    Write-Error "ArcReel 后端基线失败，退出码 $code"
    exit $code
}
Write-Output 'ArcReel backend baseline verified: Ubuntu-compatible Python 3.12, marker=not e2e'
