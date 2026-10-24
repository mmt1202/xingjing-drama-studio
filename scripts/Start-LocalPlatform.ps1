param(
    [ValidateSet('start', 'status', 'stop')]
    [string]$Action = 'start',
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'local_platform.py'
$launcherArgs = @('run', 'python', $launcher, $Action)
if ($NoFrontend) {
    $launcherArgs += '--no-frontend'
}
Push-Location -LiteralPath $projectRoot
try {
    & uv @launcherArgs
    $launcherExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $launcherExitCode
