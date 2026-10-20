# Runs one scheduled job and appends its output to data/logs/<date>-<job>.log.
# Called by Windows Task Scheduler (see scripts/install-tasks.ps1).
param(
    [Parameter(Mandatory)][ValidateSet("wheel", "daily", "sync")][string]$Job
)
$ErrorActionPreference = "Continue"  # stderr from Python (warnings) must not abort the job

$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
}
$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("{0:yyyy-MM-dd}-{1}.log" -f (Get-Date), $Job)

function Invoke-Trader([string[]]$TraderArgs) {
    "`n=== $(Get-Date -Format s) trader $($TraderArgs -join ' ')" | Out-File $log -Append -Encoding utf8
    & $uv run trader @TraderArgs 2>&1 | ForEach-Object { "$_" } | Out-File $log -Append -Encoding utf8
    "=== exit code $LASTEXITCODE" | Out-File $log -Append -Encoding utf8
}

switch ($Job) {
    "wheel" { Invoke-Trader @("wheel", "--execute") }
    "daily" {
        Invoke-Trader @("run", "trend-spy", "--execute")
        Invoke-Trader @("run", "swing-us-stocks", "--execute")
    }
    "sync" { Invoke-Trader @("sync") }
}
