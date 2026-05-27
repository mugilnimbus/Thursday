param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$controlScript = Join-Path $scriptDir "thursday.ps1"

if ($Force) {
    & $controlScript reset-workspace -Force
} else {
    & $controlScript reset-workspace
}
