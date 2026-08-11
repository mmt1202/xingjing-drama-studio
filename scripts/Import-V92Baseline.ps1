[CmdletBinding()]
param(
    [string]$Source = 'D:\aivideo\docs\v9.2-development-baseline',
    [string]$Destination = (Join-Path $PSScriptRoot '..\docs\product\v9.2')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Source)) {
    throw "找不到 v9.2 规格源: $Source"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$sourceRoot = (Resolve-Path -LiteralPath $Source).Path
$destinationRoot = [IO.Path]::GetFullPath($Destination)

Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
    $relative = [IO.Path]::GetRelativePath($sourceRoot, $_.FullName)
    if ($relative -like 'tools\*') {
        return
    }
    $target = Join-Path $destinationRoot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
}

$files = Get-ChildItem -LiteralPath $destinationRoot -Recurse -File |
    Where-Object Name -ne 'baseline.sha256' |
    Sort-Object FullName
$manifest = foreach ($file in $files) {
    $relative = [IO.Path]::GetRelativePath($destinationRoot, $file.FullName).Replace('\', '/')
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    "$hash  $relative"
}
Set-Content -LiteralPath (Join-Path $destinationRoot 'baseline.sha256') -Value $manifest -Encoding utf8NoBOM

Write-Output "Imported $($files.Count) v9.2 specification files"
